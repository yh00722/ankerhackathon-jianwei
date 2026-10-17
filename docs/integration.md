# 与官方示例工程对接

本仓库只放我们写的文件。网页和安卓部分是在赛事方发放的官方示例工程上改出来的；官方示例工程、SDK 和文档版权归发布方所有、仅限比赛使用，不在这里。下面写明我们的文件放在哪里，以及改了哪些官方文件。

## 网页（官方 Web SDK 示例工程）

1. 把 `web/src/` 下的文件按同样的相对路径复制进官方示例工程的 `src/`。
2. 我们改过的官方文件（不在本仓库，只说明改动）：

| 官方文件 | 改动 |
|---|---|
| `src/main.tsx` | 按网址 `#/` 路由：`#/elder`、`#/family`、`#/trends`、`#/share`、`#/therapist` 直接渲染我们的页面，不加载摄像头 SDK；其他地址是控制台，先调用 `installLowLatency()`（必须在 SDK 创建 RTCPeerConnection 之前），再加载 SDK |
| `src/App.tsx` | 打开页面就用 `.env.local` 里本队的凭证自动连接 T8171（网址带 `?manual` 时保留手动填写）；控制台换成 `ConsolePanel` |
| `src/components/LivePanel.tsx` | 断流 1.5 秒后自动重连；画面卡住时按 12 → 48 秒退避整个重连；官方播放器缩成小窗，主画面是控制台里的 AI 画面 |
| `vite.config.ts` | 把 `/api`、`/ws` 代理到分析服务 `127.0.0.1:8000` |

3. 凭证写在 `.env.local`（`VITE_EUFY_*`），不进仓库。
4. 控制台要用 Chrome。比赛现场的网络下，要用 `--disable-features=WebRtcPqcForDtls` 启动 Chrome 才能建立连接。

## 安卓（官方 App SDK 示例工程）

1. 把 `android/` 下三个 `jianwei/` 目录按同样的路径放进官方示例工程的 `ESIotKit-DemoApp-Common/sample-core/src/`。
2. 我们改过的官方文件：

| 官方文件 | 改动 |
|---|---|
| `main/mega/MegaMainScreen.kt` | 底部栏只放「首页 / 趋势 / 分享」，交给 `JianweiTabs(selectedItem)`；默认打开首页（老人端）。长按首页顶栏标题进入开发者模式，才显示示例工程原来的 Devices / Events / Things；图标和文字放大 |
| `src/androidMain/AndroidManifest.xml` | 加 `VIBRATE` 权限；声明 `TTS_SERVICE` 查询（安卓 11 起要声明才能找到系统语音合成引擎） |
| `main/mega/login/business/BusinessLoginConfig.kt` | 登录用的 USER_ID / TOKEN 改为引用本机的 `LocalSecrets.kt`（不进仓库） |

3. 手机和电脑用 USB 连接：`adb reverse tcp:5173 tcp:5173` 和 `adb reverse tcp:8000 tcp:8000`（`scripts/adb-reverse-loop.sh` 在重新插线后自动重设）。App 里的网页打开 `http://localhost:5173/#/elder` 等地址，设备桥访问 `http://localhost:8000`。

## 摄像头与本机环境

- 设备：eufy T8171 云台摄像头。预置位约定：1 日常区、2 训练位、3 隐私位（朝墙）、4 正面训练位；在控制台对准后点「存为」保存。
- `scripts/start.sh` 一条命令启动分析服务、网页、adb reverse、演示用 Chrome 和看门狗。它用到仓库旁边的两个本机脚本，没有放进来：`env.sh`（切到 JDK 17、Android SDK、Python 虚拟环境）和 `start-chrome.sh`（用独立资料目录、带上面的参数启动 Chrome）。
