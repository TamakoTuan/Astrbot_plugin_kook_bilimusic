# Astrbot_plugin_kook_bilimusic

<p align="center">
  KOOK B 站点歌
</p>

<p align="center">
  在 KOOK 语音频道里点 B 站歌曲，机器人自己进房播放，播完自动退出。
</p>

<p align="center">
  <img alt="GitHub last commit" src="https://img.shields.io/github/last-commit/TamakoTuan/Astrbot_plugin_kook_bilimusic?logo=github">
  <a href="https://github.com/TamakoTuan/Astrbot_plugin_kook_bilimusic/releases"><img alt="Release" src="https://img.shields.io/github/v/release/TamakoTuan/Astrbot_plugin_kook_bilimusic"></a>
  <img alt="License" src="https://img.shields.io/badge/License-MIT-blue">
  <img alt="GitHub stars" src="https://img.shields.io/github/stars/TamakoTuan/Astrbot_plugin_kook_bilimusic?style=social">
</p>

<p align="center">
  <img alt="AstrBot" src="https://img.shields.io/badge/AstrBot-%3E%3D4.26-6f42c1">
  <img alt="KOOK" src="https://img.shields.io/badge/Platform-KOOK-6f42c1">
  <img alt="Python" src="https://img.shields.io/badge/Python-%3E%3D3.10-3776ab">
  <img alt="ffmpeg" src="https://img.shields.io/badge/ffmpeg-libopus-007808">
</p>

---

## 简介

`Astrbot_plugin_kook_bilimusic` 是 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 KOOK 语音频道点歌插件。

可以让你的AI自己搜索 B 站、解析 DASH 音源、用 ffmpeg 推流到 KOOK 语音频道。

- 选歌卡片：封面、标题、时长、UP 主，点按钮即可点歌
- 支持连续点选多首，自动排队播放
- 「正在播放」卡片带倒计时进度，以及切歌 / 停止 / 队列按钮
- 也支持斜杠指令和自然语言：既可以用 `/点歌 晴天`，也可以直接说「来一首晴天」
- 自动定位点歌人所在语音频道，播完自动退出

## 功能

| 能力 | 说明 |
| --- | --- |
| B 站搜索 | 匿名 WBI 搜索，不需要登录 B 站 |
| 选歌卡片 | 每首歌带封面、标题、时长、UP 主和「点歌」按钮 |
| 连续多选 | 60 秒内可以连续点几首，点几次加几首 |
| 播放队列 | 自动顺序播放，`/队列` 查看当前和待播 |
| 播放进度 | KOOK 原生倒计时模块显示剩余时间 |
| 切歌 / 停止 | 卡片按钮或 `/切歌`、`/停止` |
| BV / AV 直达 | `点歌 BV1xx411c7mD`、`点歌 av170001` |
| 自然语言 | 注册 LLM 工具 `kook_voice_play` |
| 封面转存 | 封面统一转 512px JPEG 再上传 KOOK，避免 WebP 格式错误 |
| 自动进出房 | 自动查点歌人所在语音频道，播完自动退出 |

## 效果示例

```text
你： /点歌 晴天
Bot： [选歌卡片]
      (1) 晴天                    [点歌]
      (2) 晴天（4K修复）           [点歌]
      ...

你： 点击 (1) 的「点歌」
Bot： 已加入队列：晴天

你： 再点 (2)
Bot： 已加入队列：晴天（4K修复）

Bot： [正在播放卡片] 晴天 · 周杰伦
      [倒计时 04:30]
      [切歌] [停止] [队列]

你： /队列
Bot： [队列卡片]

你： /切歌
Bot： 已切歌。
```

## 安装

### 前置条件

- AstrBot `>= 4.26`（已在 `4.29.3` 上验证）
- Python `>= 3.10`
- `ffmpeg`，且带 `libopus` 编码器
- KOOK 机器人角色拥有 **连接语音频道** 权限

检查 ffmpeg：

```bash
ffmpeg -version
ffmpeg -h encoder=libopus
```

### 方式一：从插件市场安装

插件发布到 AstrBot 插件市场后，可在 WebUI →「插件」→「插件市场」搜索 `KOOK 点歌` 或 `B站点歌` 直接安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/TamakoTuan/Astrbot_plugin_kook_bilimusic.git
```

或把 `Astrbot_plugin_kook_bilimusic-1.2.2.zip` 解压到 `AstrBot/data/plugins/`。

然后重启 AstrBot，或在 WebUI 插件页点击「重载」。

### 依赖

无额外 Python 依赖，`aiohttp` 由 AstrBot 自带。

外部依赖只有 `ffmpeg`，需要能在 AstrBot 进程的 `PATH` 中找到；也可以在插件配置里手动填写路径。

## 首次使用

1. 在 KOOK 服务器设置 → 角色权限里，给机器人角色勾选 **连接语音频道**；
2. 进入一个语音频道；
3. 在文字频道发送：

   ```text
   /点歌 晴天
   ```

4. 在卡片上点「点歌」加入队列；
5. 机器人进房播放，播完自动退出。

> 命令前缀跟随 AstrBot 的 `wake_prefix`，默认 `/`，也可以 @机器人。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/点歌 <关键词>` | 搜索并发送选歌卡片 |
| `/直接点歌 <关键词>` | 跳过选择，把第一个结果加入队列 |
| `/队列` | 查看当前播放和剩余队列 |
| `/切歌` | 跳过当前歌曲，播下一首 |
| `/停止` | 停止播放并清空队列 |
| `/kook语音状态` | 查看 ffmpeg、当前播放和语音频道 |
| `/点歌 BV1xx411c7mD` | 用 BV 号点歌 |
| `/点歌 av170001` | 用 AV 号点歌 |

### 多选

- 卡片上连续点「点歌」，每次加一首；
- 文字回复支持 `1`、`1 2 3`、`1,2`、`1-3`；
- 回复「取消」结束本次选歌。

## 卡片交互

### 选歌卡片

```text
🎵 B 站搜索结果
搜索 晴天 · 60 秒内点击按钮加入队列，可连续点选多首
──────────────────────────────
(1) 晴天                              [点歌]
[封面] 4:30 · 周杰伦
──────────────────────────────
(2) 晴天（4K修复）                     [点歌]
[封面] 5:17 · UP主
```

- 按钮 value 形如 `kvp:<token>:<序号>`，token 60 秒有效，只有发起人可点；
- 封面会先转存 KOOK，失败时自动省略封面但保留标题和按钮。

### 正在播放卡片

```text
▶ 正在播放
──────────────────────────────
[封面] 晴天
       周杰伦
       4:30
[倒计时：03:57]
[切歌] [停止] [队列]
语音频道：一房 · 队列还有 2 首
```

- 倒计时使用 KOOK 原生 `countdown` 模块；
- 「切歌」立即结束当前 ffmpeg 并播下一首；
- 「停止」结束播放并清空队列。

### 队列卡片

```text
🎶 播放队列
──────────────────────────────
▶ 正在播放
晴天 — 周杰伦
──────────────────────────────
1. 夜曲 — 周杰伦
   时长 3:46
──────────────────────────────
[切歌] [停止] [队列]
```

最多显示 8 首待播，超出显示「还有 N 首未显示」。

## 自然语言

插件向 AstrBot 注册了 `kook_voice_play` 工具，模型会自动调用：

- 「来一首晴天」
- 「放首周杰伦的歌」
- 「点一首适合晚上听的」

> `/点歌` 这类命令消息不会触发工具，避免没选歌就自动播放第一首。

## 配置

在 AstrBot WebUI →「插件」→「KOOK B 站点歌」→「配置」中修改：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enable` | bool | `true` | 总开关 |
| `search_limit` | int | `5` | 搜索候选数量，1–10 |
| `selection_timeout` | int | `60` | 选歌卡片/序号等待秒数 |
| `max_duration_minutes` | int | `30` | 超过此时长的视频会被过滤 |
| `ffmpeg_path` | string | 空 | 留空则用 PATH 里的 ffmpeg |
| `default_bitrate` | int | `48000` | 兜底码率；优先用 KOOK 返回的 `bitrate` |
| `volume` | float | `0.8` | 推流音量 |
| `max_play_seconds` | int | `1800` | 单曲播放兜底超时 |
| `announce` | bool | `true` | 是否发送正在播放卡片和结束提示 |

## 工作原理

```text
KOOK 文字频道
  → /点歌 晴天
  → B 站 WBI 搜索 + 封面转存 KOOK
  → 选歌卡片
  → 点击「点歌」加入队列
  → channel-user/get-joined-channel 查点歌人所在语音频道
  → voice/join 拿 RTP 推流地址
  → ffmpeg 转 Opus 推流
  → 播完 voice/leave
```

- 全部 KOOK 请求走 AstrBot KOOK 适配器的 HTTP 会话，自带 `Authorization`；
- 语音频道用 `channel-user/get-joined-channel` 一次请求查到，不会遍历全服；
- 播放进度使用 KOOK 卡片的 `countdown` 模块，不需要定时刷新消息；
- 封面统一转换成 512px JPEG 再上传 `asset/create`，规避 B 站 WebP 封面。

### 用到的接口

- **B 站**：`/x/web-interface/nav`、`/x/web-interface/wbi/search/type`、`/x/web-interface/view`、`/x/player/playurl`
- **KOOK**：`channel-user/get-joined-channel`、`channel/view`、`asset/create`、`message/create`、`voice/join`、`voice/keep-alive`、`voice/leave`
- **本地**：`ffmpeg`

## 常见问题

**刚发 `/点歌` 就自动播放了第一首？**

1.2.1 已修复：命令消息也会进入 LLM，模型可能顺手调用 `kook_voice_play`。现在命令会阻止事件继续传播，工具也会拒绝处理命令消息。

**提示「查询语音频道失败：系统检测到您的登录环境异常」？**

旧版本会遍历全服语音频道，容易触发 KOOK 风控。当前版本使用 `channel-user/get-joined-channel` 一次请求定位，并复用适配器会话。如果仍然出现，检查机器人 token 是否有效，或稍等几分钟再试。

**卡片发不出来 / 40011？**

旧版本会把卡片发到用户 ID（私聊）而不是频道，导致 `40011`。当前版本已修正 `target_id`。如果仍然失败，插件会自动降级：封面转存后重发 → 去掉封面重发 → 纯文字列表。

**卡片没有封面？**

B 站封面会转成 512px JPEG 再上传 KOOK；上传失败时自动省略封面，标题和按钮不受影响。

**没有声音？**

1. `ffmpeg -h encoder=libopus` 是否有输出；
2. 机器人是否有「连接语音频道」权限；
3. 服务器是否能向 KOOK 返回的 `ip:port` 发 UDP；
4. 频道码率是否被 KOOK 限制（插件使用 join 返回的 `bitrate`）。

**切歌后要等 2–3 秒？**

KOOK 一次 `voice/join` 只能推一次流，换歌必须重新进房，属于接口限制。

**多 P 视频只播第一段？**

当前默认播放 P1。需要指定分 P 可以先用 BV 号点歌，后续版本再扩展。

**队列重启后没了？**

队列只保存在内存中，AstrBot 重启后会清空。

## 目录结构

```text
Astrbot_plugin_kook_bilimusic/
├── main.py              # 插件入口：指令、卡片交互、队列与播放调度
├── bilibili.py          # WBI 签名、搜索、视频详情、DASH 音源
├── voice.py             # KOOK 语音接口、找人、ffmpeg RTP 推流
├── cards.py             # 选歌 / 正在播放 / 队列卡片 JSON
├── metadata.yaml        # 插件元信息
├── _conf_schema.json    # 插件配置项定义
├── logo.png
├── README.md
├── CHANGELOG.md
└── LICENSE
```

## 开发

```bash
git clone https://github.com/TamakoTuan/Astrbot_plugin_kook_bilimusic.git
cd Astrbot_plugin_kook_bilimusic

python -m py_compile main.py bilibili.py voice.py cards.py
```

没有第三方 Python 依赖，`aiohttp` 由 AstrBot 自带。修改后把目录放到 `AstrBot/data/plugins/` 下，在 WebUI 里重载插件即可。

## 已知限制

- 一个机器人同一时间只能待在一个语音频道；
- 换歌需要重新 join，中间有 2–3 秒空档；
- 带密码的语音频道无法加入；
- 未登录 B 站时部分视频没有音源；
- 多 P 视频默认 P1；
- 队列在内存中，AstrBot 重启后清空；
- KOOK 对推流码率、并发和卡片媒体转存有配额限制。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件框架
- [KOOK 开发者中心](https://developer.kookapp.cn/) —— 语音推流、卡片消息和机器人接口文档

## 许可证

[MIT](LICENSE)

你可以自由使用、修改、分发本项目，但需保留版权声明和许可声明。
因使用本项目产生的任何法律责任由使用者自行承担。

---

<p align="center">
  <b>Author: TamakoTuan</b> · Made with ❤️ for KOOK
</p>
