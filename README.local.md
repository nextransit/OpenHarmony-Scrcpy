# ohscrcpy 本地安装（Linux x86_64）

## 已装好

| 组件 | 位置 | 状态 |
|------|------|------|
| 客户端脚本 | `/home/ben/ohscrcpy/ohscrcpy_client.py` | ✅（z-rong/ohscrcpy，OpenCV 内核） |
| 设备端二进制（RISC-V 示例） | `/home/ben/ohscrcpy/ohscrcpy` | ⚠️ 架构与 aarch64 设备不匹配 |
| Python 虚拟环境 | `/home/ben/ohscrcpy/venv`（opencv 5.0 / pygame 2.6 / numpy） | ✅ |
| 启动脚本 | `/home/ben/ohscrcpy/run.sh` | ✅ |

## 用法

```bash
# 单设备（自动识别 hdc 在线设备）
cd /home/ben/ohscrcpy && ./run.sh

# 指定设备（多设备时）
cd /home/ben/ohscrcpy && ./run.sh -t 192.168.26.160:5555
```

## 重要限制（本机为 Linux，缺少两样东西）

1. **设备端二进制必须为 aarch64**
   仓库自带 `ohscrcpy` 是 RISC-V 64（musl），本机设备 AIOT-3568A 是 aarch64（arm64-v8a），**直接推上去无法运行**。
   编译需要 OpenHarmony **全量源码** + `--product-name rk3568 --build-target ohscrcpy`，
   本机只有 NDK/SDK 无源码树，无法在此编译。

2. **snowlyg/ohscrcpy-releases（GUI 版）仅支持 Windows x64**
   官方 Releases 只有 `ohscrcpy-windows-x64-<v>.zip`，本机 Linux 无法直接运行。

## 现状结论

- 「本地安装 ohscrcpy」的 **PC 端**已完成（venv + 脚本 + 启动器）。
- 要真正投屏到本机，还需 **aarch64 设备端二进制**——来源两个：
  1. 有 OHOS 源码树/编译环境的机器编译 `ohscrcpy` 后替换本目录同名文件；
  2. 或有现成 aarch64 预编译产物（当前公开渠道未检索到）。
