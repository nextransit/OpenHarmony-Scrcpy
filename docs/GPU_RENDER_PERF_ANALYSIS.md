# render_service CPU 占用与 GPU 使用情况调研 (2026-10-28)

## 1. 结论速览

| 项 | 状态 |
|---|---|
| GPU 型号 | AMD Radeon (amdgpu, PCI 1002:699F = Navi14/RX5500 系), Loongson 3A6000 整机 |
| GPU 利用率 | **0%** (投屏抓屏时也是 0%) —— GPU 完全闲置 |
| GPU 频率 | sclk 停在最低档 1183MHz / 1.?GHz; mclk 300MHz; 档位表 214~1183MHz |
| 显存 | 183MB used / 2048MB total (2GB 独显) |
| render_service CPU | 空闲 ~48%, **投屏/抓屏时 50-61%** |
| 并发吃 CPU 的其它 | `kh_telemetry_engine` ~55%, `kh_l5_cmd`/`kh_l5_*` kworker ~40-66% (Kaihong 遥测/诊断) |
| 设备负载 | 4.9-5.x (8 核); 内存 free ~3.9G/7.3G (健康) |
| 投屏抓屏进程 | jpeg_server `-w0 -h0 -n 4`; input_agent 常驻 |

## 2. 关键事实

### 2.1 GPU 是 AMD 独显 (不是 Mali)
```
DRIVER=amdgpu   PCI_ID=1002:699F   PCI_SLOT_NAME=0000:07:00.0
/dev/dri/card0  /dev/dri/renderD128
/sys/class/drm/card0/device/gpu_busy_percent => 0
pp_dpm_sclk: 214/551/734/980/1046/1098/1124/1183MHz (当前 * 1183)
pp_dpm_mclk: 300/625/1500MHz (当前 * 300)
mem_info_vram_used  => 192446464 (183MB)
mem_info_vram_total => 2147483648 (2GB)
```

### 2.2 投屏抓屏时 render_service 高 CPU 但 GPU 0%
三次 `top -b -n 1` 采样期间 jpeg_server 在抓 1080p:
```
render_service 61.5%  / 50.0%  / 59.2%    (R 状态, 真实在跑)
jpeg_server   ~0.0%                       (fork+exec snapshot_display 本身很轻)
gpu_busy_percent => 0                      (投屏中仍为 0)
```

结论: **渲染压力全部在 CPU 侧**, GPU 没有参与 —— 高度怀疑 render_service 走的是
CPU 合成 / 软件渲染 (llvmpipe 或缺 `-gpu` 后端), 而 amdgpu DRI 未被使用树莓。

### 2.3 系统遥测也在抢 CPU
```
kworker/u16:1 kh_l5_cmd          66.6%
kworker/u16:4 kh_telemetry_engine 55.5%
```
Kaihong 自带的 telemetry / DID S / 诊断 kworker 长期高占用, 与渲染叠加后
造成 8 核设备 load>=5, 更放大了抓屏帧率问题.

## 3. 待验证 (设备恢复后)
- [ ] render_service 是否加载 amdgpu DRI (mesa radeonsi/navi), 还是 llvmpipe 软件渲染
- [ ] `glxinfo`-类工具 / `dmesg` amdgpu 初始化错误
- [ ] 关闭 Kaihong telemetry (persist.khmonitor.* / kh_diagd) 后 load 与帧率变化
- [ ] snapshot_display 抓屏是否本可走 GPU 合成 (OH_AVScreenCapture) 而非 snapshot_display

## 4. 证据原始帧
- 抓屏 (桌面含"未激活"水印 vs patch 后无水印): 归一化互相关 NCC 从高 -> 0.103
