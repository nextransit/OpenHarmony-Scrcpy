# 设备周期性重启 — licenseMonitor reboot patch

> 目标设备: Kaihong BotStation (`const.product.model = loongson_uefi`, loongarch64)
> 现象: 设备每约 **59~61 分钟**自动重启一次, 与是否投屏无关
> 结论: **厂商许可校验 (licenseMonitor) 判定失败后主动请求重启**

---

## 1. 现象与证据链

### 1.1 重启是"软件主动请求"，不是崩溃

重启前的 kernel 日志 (`/data/log/hilog/kmsg.319.20261026-205357.gz` 末尾) 显示的是
**init 的有序关机流程**，而不是 panic / watchdog / 掉电:

```
21:54:16.480 [pid=1][Init][INFO][init_service_manager.c:1395]StopAllServices stop service user_auth_host
21:54:16.480 [pid=1][Init][INFO][init_service_manager.c:792]stop service user_auth_host, pid 820.
21:54:16.483 ... StopAllServices stop service camera_host ...
```

- 无 `panic` / `oops` / `watchdog` / `thermal` 关键字
- 无 `cppcrash` (最新 cppcrash 停留在 10-25，与本次重启无关)
- 日志在 21:54:16 戛然而止 —— 连写日志的进程也同时停止，属于**整机级停止**

### 1.2 关机前一瞬间的调用者

```
21:54:16.402 audit: avc: denied { call } for pid=1123 comm="licenseMonitor"
               scontext=u:r:licenseMonitor:s0 tcontext=u:r:accountmgr:s0 tclass=binder
21:54:16.480 init: StopAllServices ...            <- 关机开始
```

`licenseMonitor` 在关机前 78ms 触发了最后一次 binder 调用。

### 1.3 精确的 59 分钟周期

`persist.license.monitor.interval = 59` (分钟) — 厂商许可监控周期。

| hilog 序号 | 启动时刻 | 上一轮运行时长 |
|---|---|---|
| kmsg.316 | 17:50:49 | — |
| kmsg.317 | 18:51:52 | 60 分 00 秒 |
| kmsg.318 | 19:52:55 | 60 分 13 秒 |
| kmsg.319 | 20:53:57 | 60 分 13 秒 |
| (空载实测) | 21:52:50 | 58 分 53 秒 (**无投屏客户端仍重启**) |

> 最后一行是关键: 21:52:50 那次重启期间**没有任何 ohscrcpy 客户端在跑**，
> 证明重启与投屏无关。

### 1.4 许可校验持续失败

```
21:24:15.828 E C0d700/LICENSE: [license.cpp(570)] Read license data from factory fail.
21:24:25.831 E C0d700/LICENSE: [license.cpp(570)] Read license data from factory fail.
... 每 10 秒一次, 持续约 59 分钟 ...
21:24:15.828 I C0d700/LICENSE: [license_monitor_main.cpp(43)] SendNotificationtoUI VerifyResult: <private>, TrialTimeLeft: <private>
```

`/data/license/failed_collect.json` 也记录了一次许可采集失败 (当天 04:35)。

---

## 2. 根因

### 2.1 factory 分区存在但未挂载

设备分区表里有 factory 分区，设备节点存在:

```
/dev/block/by-name/factory -> /dev/block/nvme0n1p7     (512 MB)
```

但 `mount` 输出里**没有 factory**，且读出的超级块内容完整:

```
$ dd if=/dev/block/nvme0n1p7 bs=1024 count=2 | od -c
... 004   / f a c t o r y  ... / d a t a / l o c a l / t m p / f a c t o r y
```

→ 分区里**有完整的 ext4 许可数据**，只是**没被挂载到 `/factory`**。

### 2.2 根因: licenseMonitor 硬编码的 fstab 文件名不匹配本板卡

反汇编 `/system/bin/licenseMonitor` (LoongArch, stripped) 得到的三条硬编码路径:

```
/vendor/etc/fstab.sky1_evb
/vendor/etc/fstab.d3000
/vendor/etc/fstab.d2000
```

而**本设备实际使用**的 fstab 是:

```
$ ls /vendor/etc/fstab*
/vendor/etc/fstab.3a6000          <- 3a6000 板卡ID
```

init 侧没问题 — 它用模板 `/vendor/etc/fstab.%s` (`%s` 来自 `/system/etc/fstab.required`)
自动匹配到 `fstab.3a6000`；但 **licenseMonitor 用的是写死的三个文件名，没有 `3a6000`**。

于是 licenseMonitor 的判定链:

```
1. param get ohos.required_mount.factory=        -> 空 (厂家未给 3a6000 适配该 param)
2. 依次尝试 fstab.sky1_evb / d3000 / d2000        -> 全部不存在
3. 在 fstab 中找含 '/factory' 的行                -> "Line containing '/factory' not found."
4. GetFactoryBlock 失败                           -> "GetFactoryBlock failed, path: %s"
5. Read license data from factory fail.            -> 校验失败
6. 约 59 分钟后: "CheckLicense fail, reboot soon!"
7. SetParameter("ohos.startup.powerctrl", "reboot")
8. PowerMgr 执行有序关机 -> init StopAllServices -> 设备重启
9. 重启后 factory 仍未挂载 -> 回到第 1 步, 无限循环
```

---

## 3. 反汇编定位 (licenseMonitor)

### 3.1 失败路径的反汇编

`license_monitor_main.cpp` 对应代码 (vaddr):

```asm
0x1bc08  ori   s6, zero, 0x1          ; s6 = 1
0x1bc0c  move  a0, zero               ; arg0 = 0
0x1bc10  move  a1, s6                 ; arg1 = 1
0x1bc14  bl    VerifyLicense          ; 返回值 1 = 校验通过
0x1bc18  move  s5, a0
0x1bc1c  beq   a0, s6, 0x2fc          ; ★ 校验通过 -> 跳到成功路径
0x1bc20  ...                          ; 校验失败 -> 继续往下
...
0x1bc50  bl    HiLogPrint             ; "CheckLicense fail, reboot soon!"
...
0x1beb0  bl    SetParameter           ; SetParameter("ohos.startup.powerctrl", "reboot")
```

成功路径入口 `0x1bf18` (sleep 后继续循环, 不重启):

```asm
0x1bf18  lu12i.w a0, 0x540be
0x1bf20  lu32i.d a0, 0x2
0x1bf2c  bl    std::this_thread::sleep_for(...)
```

### 3.2 关键字符串与其引用点

| 字符串 | vaddr | 引用指令 |
|---|---|---|
| `CheckLicense fail, reboot soon!` | 0xe437 | 0x1bc48 |
| `Line containing '/factory' not found.` | 0xe0a0 | 0x3eb70 |
| `Read license data from factory fail.` | 0xdb42 | 0x2bca8 |
| `/vendor/etc/fstab.sky1_evb` | 0xcf69 | 0x3e82c |
| `/vendor/etc/fstab.d3000` | 0xd9a7 | 0x3e7e8 |
| `/vendor/etc/fstab.d2000` | 0xd9e6 | 0x3e7a4 |
| `reboot` | 0xbf4d | 0x1be8c |
| `ohos.startup.powerctrl` | 0xc743 | 0x1be60 |

### 3.3 LoongArch B 指令编码 (已用二进制内 6 处真实样本验证)

```
word = 0x50000000 | (((off >> 2) & 0xFFFF) << 10) | (((off >> 2) >> 16) & 0x1F)
```

样本校验:

| 地址 | 原始字节 | word | off |
|---|---|---|---|
| 0x19014 | 00040050 | 0x50000400 | 0x4 |
| 0x1914c | 00345b52 | 0x525b3400 | 0x25b34 |
| 0x1925c | 00245a52 | 0x525a2400 | 0x25a24 |
| 0x19304 | 007c5952 | 0x52597c00 | 0x2597c |
| 0x19370 | 00105952 | 0x52591000 | 0x25910 |
| 0x19420 | 00605852 | 0x52586000 | 0x25860 |

---

## 4. Patch 方案

把 `0x1bc1c` 的**条件分支**改成**无条件跳转**，让校验失败时也走成功路径，
从而永远不执行 `SetParameter("ohos.startup.powerctrl", "reboot")`。

| | 指令 | 字节 (小端) |
|---|---|---|
| 原始 | `beq a0, s6, 0x2fc` | `58 02 fc 9d` |
| Patch 后 | `b 0x2fc` | `00 fc 02 50` |

### 4.1 部署方式: 原地替换 `/system/bin/licenseMonitor` (唯一可行)

**不能用** "patched 副本放 `/data/local/tmp` + 改 init cfg 的 path 指过去" 的做法。
实测该做法会让**设备彻底起不来** (端口全关、无法 ping/hdc)，原因有三:

1. **SELinux 域切换被拒绝** — `/system/bin/licenseMonitor` 的标签是
   `licenseMonitor_exec`，init 以 `secon: u:r:licenseMonitor:s0` 启动它；
   而 `/data/local/tmp` 下文件的标签是 `data_local_tmp`，
   **域切换要求可执行文件标签为 `licenseMonitor_exec`**，否则 init 拉起失败。
2. **库搜索路径 / 工作目录** — init 为 `/system/bin` 下的服务设置了
   `LD_LIBRARY_PATH` 等环境，换路径后依赖的 `libkh_license_native.z.so`
   等系统库可能加载失败。
3. 该服务的 `critical` 配置为 `[1, 1, 10]`，启动失败会触发 init 的重启策略。

**正确做法**（当前脚本 `--enable` 采用）:

```
1. 备份原二进制 -> /data/local/tmp/license_patch/licenseMonitor.system.bak
2. mount -o remount,rw /
3. cp patched -> /system/bin/licenseMonitor.new
   chmod 755 /system/bin/licenseMonitor.new
   chcon u:r:licenseMonitor_exec:s0 /system/bin/licenseMonitor.new   # 还原上下文
   mv -f /system/bin/licenseMonitor.new /system/bin/licenseMonitor   # 原子替换
4. init cfg 保持原样 (path 本来就是 /system/bin/licenseMonitor)
5. kill -9 $(pidof licenseMonitor)   -> init 按原 cfg 重启它
6. mount -o remount,ro /
```

其它安全措施:

- 原二进制 + init cfg 双重备份，`--disable` 一键还原
- 幂等：已打过 patch 的文件会识别并跳过
- 带上下文校验 (`bl VerifyLicense` 前后各 4 字节)，二进制版本不匹配时拒绝执行
- 依赖库检查：patched 二进制依赖的 18 个 `.z.so` 全部在 `/system/lib64`，原地替换不影响加载

### 为什么不直接修 fstab

修 fstab (给 `fstab.3a6000` 加 factory 挂载行) 是更"正确"的做法，但:

1. `/vendor` 与 `/system` 都是只读分区，改动需 remount，且会被 OTA 覆盖
2. factory 分区内容是厂商签名的许可数据，挂载后是否校验通过未经验证
3. licenseMonitor 走的是 `hdf_factory_driver` / `GetFactoryBlock` 路径，
   挂载 `/factory` 是否满足其读取路径未经验证

patch 方案直接命中重启触发点，行为确定、可回滚、可验证。

---

## 5. 使用

脚本: `scripts/license_patch/patch_license_monitor.py`

```bash
# 1) 看设备当前状态 (不改任何东西)
python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 --status

# 2) 拉取 + 生成 patched 副本到本地
python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \
    --pull --output out/licenseMonitor.patched

# 3) 推送到设备用户态目录 + 启用 (原地替换 /system/bin/licenseMonitor)
python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 \
    --pull --push --enable

# 4) 回滚
python3 scripts/license_patch/patch_license_monitor.py --device 192.168.19.9:8710 --disable
```

验证:

```bash
# 确认已替换 (SELinux 上下文应为 licenseMonitor_exec)
hdc -t 192.168.19.9:8710 shell "ls -laZ /system/bin/licenseMonitor"

# 确认进程正常 (init 重启后应存在)
hdc -t 192.168.19.9:8710 shell "pidof licenseMonitor"

# 确认不再重启 (跨过下一个 59 分钟窗口, uptime 应持续增长)
hdc -t 192.168.19.9:8710 shell "cat /proc/uptime"

# 确认日志里仍有校验失败但不再重启
hdc -t 192.168.19.9:8710 shell "hilog | grep -i 'CheckLicense'"
```

> 注意: 许可校验仍会失败 (HiLog 仍打印 `CheckLicense fail, reboot soon!`),
> 只是不再触发重启 —— 这是本 patch 的预期行为。

---

## 6. 影响与边界

- **只影响许可校验的重启行为**，不影响投屏、输入代理、编解码等 ohscrcpy 功能
- 许可校验仍会失败 (HiLog 仍会打印 `Read license data from factory fail.` 与
  `CheckLicense fail, reboot soon!`)，只是**不再触发重启**
- 属于**规避厂商许可保护**的行为，仅适用于本项目的研发调试设备；
  正式出货设备应由厂商在 `fstab.3a6000` 中补齐 factory 挂载行
- 若后续厂商固件修复了 fstab，可直接 `--disable` 撤掉 patch
