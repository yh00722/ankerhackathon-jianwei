# 见微知行 · 决赛作品

关节置换术后老人居家康复的「行动与训练观察」。老人主动开启后，eufy 摄像头转到授权位置，视觉 AI 把画面变成三类事实：完整看到 / 没看全 / 没看到。结果先给老人，老人授权后家属才能看到摘要。

任务与验收标准见 [TASKS.md](TASKS.md)。

## 结构

| 目录 | 内容 |
|---|---|
| `server/` | Python 分析服务（FastAPI + MediaPipe + SQLite）：接收帧、识别姿态与起身、管理会话、生成报告、处理分享授权、给设备桥下发指令 |
| `web/` | 网页（基于官方 Web SDK demo）：`#/console` 控制台兼采集页，`#/elder` 老人端，`#/family` 家属端 |
| `android/` | 安卓 App（基于官方 App SDK demo）：设备桥负责控制、状态和事件；老人端页面用 WebView 嵌入 |
| `scripts/` | 一键启动等脚本 |

## 数据流

```
摄像头 ──WebRTC（官方通道）──▶ Chrome 控制台页（抖动缓冲之前直取编码帧 → WebCodecs 解码；不可用时退回 Web SDK 取帧）
   ▲                                  │ JPEG 帧 + 拍摄时刻（WebSocket，仅本机）
   │ 控制 / 属性 / 事件（App SDK）      ▼
安卓 App（设备桥 + 老人端 WebView）◀──adb reverse──▶ 分析服务 :8000 ──▶ SQLite（只存事件与数字）
```

- 画面只存在于浏览器和分析服务的内存中，逐帧处理后即丢弃。关键点只保留在 10 秒的内存缓冲里。
- 凭证不入库：`web/.env.local`、`android/**/LocalSecrets.kt` 都已被 `.gitignore` 忽略。
