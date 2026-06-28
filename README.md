# LiveStream-Agent

一个开箱即用的虚拟主播 AI 助手，实时接收直播间弹幕，通过大模型进行智能决策，生成带情感的语音回复。

<img src="./images/项目启动桌面视角1.png" width="89%" >


## 核心链路

```
直播间弹幕 → 平台连接器 → 消息过滤 → Agent 大脑 (LLM) → 情感引擎 → TTS 语音合成 → 本机播放
```

弹幕进来，AI 分析上下文、判断意图、选择语气，最后用带有情感色彩的真人语音说出来。整个过程实时发生，就像直播间里有一个真正的助理在帮你和观众互动。

## 架构

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────────────┐
│  直播间平台   │ →  │  Connector   │ →  │   Pipeline    │ →  │  本机播放    │
│  (弹幕/礼物)  │    │  消息接入层   │    │  处理管道      │    │  (虚拟麦克风) │
└─────────────┘    └──────────────┘    └──────┬───────┘    └─────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
              ┌─────▼─────┐           ┌──────▼──────┐           ┌──────▼──────┐
              │  Memory    │           │  LLM Brain  │           │  Emotion    │
              │  三级记忆   │◄─────────►│  大模型大脑  │──────────►│  情感引擎   │
              └───────────┘           └─────────────┘           └──────┬──────┘
                                                                      │
                                                               ┌──────▼──────┐
                                                               │  TTS + Play │
                                                               │  语音合成   │
                                                               └─────────────┘
```

整个系统遵循 **Sense → Think → Act** 的智能体循环：感知直播间输入，通过大模型思考如何回应，最终用带情感的语音执行动作。

## 功能特性

- **多平台支持**：目前支持抖音和 B站，通过插件化连接器架构可轻松扩展
- **智能决策**：LLM 输出结构化 JSON，包含回复内容、情感标签、动作类型和内心独白
- **情感语音**：7 种情感（开心、激动、平静、共情、幽默、严肃、温柔）映射为语速/音调参数，通过 edge-tts 合成自然语音
- **三级记忆**：短期记忆（滑动窗口）、工作记忆（会话内关键事实）、长期记忆（跨会话用户画像），SQLite 持久化
- **人设切换**：YAML 定义的多套主播人格，支持运行时热切换
- **防刷屏调度**：最小回复间隔、队列管理、同用户冷却、垃圾消息过滤
- **礼物感谢**：自动识别礼物事件，生成个性化感谢语
- **实时字幕**：桌面字幕叠加窗口，AI 回复时立即显示文字（先于语音）
- **弹幕滚动**：左侧弹幕窗口实时滚动显示观众弹幕，持续堆积不消失
- **会话总结**：每次直播结束后自动生成摘要存档
- **LLM 无关**：基于 OpenAI 兼容接口，支持 DeepSeek、GPT、智谱 GLM 等任意兼容 API

## 项目结构

```
LiveStream-Agent/
├── agent/                     # Agent 核心
│   ├── brain.py               # 大脑：LLM 调用、决策、响应生成
│   ├── memory.py              # 记忆系统：三级记忆 + SQLite 存储
│   ├── persona.py             # 人设管理：角色定义、Prompt 构建
│   └── emotion.py             # 情感引擎：情感标签 ↔ 语速/音调参数
│
├── connectors/                # 平台连接器（插件化）
│   ├── base.py                # 抽象基类，定义统一接口
│   ├── douyin/                # 抖音连接器（WebSocket + Protobuf）
│   └── bilibili/              # B站连接器（bilibili_api）
│
├── llm/                       # 大模型接口层（适配器模式）
│   ├── base.py                # LLM 抽象基类 + 数据结构
│   └── adapter.py             # OpenAI 兼容适配器（支持 DeepSeek/GPT/GLM）
│
├── speech/                    # 语音模块
│   ├── tts.py                 # TTS 引擎：纯文本 + 情感参数 → edge-tts → MP3
│   └── player.py              # 音频播放器
│
├── ui/                        # 界面模块
│   └── __init__.py            # 字幕叠加窗口 + 弹幕滚动窗口（Tkinter）
│
├── pipeline/                  # 消息处理管道
│   ├── orchestrator.py        # 管道编排器：串联整个处理流程
│   ├── filter.py              # 消息过滤：去重、垃圾检测、频率控制
│   └── scheduler.py           # 响应调度：队列管理、防刷屏
│
├── storage/                   # 持久化存储
│   ├── database.py            # SQLite 连接管理（aiosqlite）
│   └── models.py              # 数据模型：PipelineMessage 等
│
├── config/                    # 配置中心
│   ├── settings.yaml          # 全局配置（LLM、TTS、Pipeline 参数）
│   ├── personas/              # 人设定义（YAML）
│   │   └── default.yaml       # 默认主播人设
│   └── prompts/               # Prompt 模板
│       └── system.yaml        # 系统指令模板
│
├── utils/                     # 通用工具
│   ├── __init__.py
│   └── logger.py              # 统一日志（loguru）
│
├── data/                      # 运行时数据（数据库、音频、日志）
│
├── main.py                    # 主入口
├── requirements.txt           # 依赖清单
└── README.md
```

## 快速开始

### 环境要求

- Python 3.10+
- 一个 LLM API Key（DeepSeek / OpenAI / 智谱 GLM 任意一个）
- Windows / macOS / Linux

### 安装

```bash
# 克隆项目
git clone https://github.com/LiveStream-Agent.git
cd LiveStream-Agent

# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

编辑 `config/settings.yaml`，填入你的 LLM API 信息：

```yaml
llm:
  api_key: "sk-your-api-key-here"   # 或设置环境变量 LLM_API_KEY
  base_url: "https://api.deepseek.com"
  model: "deepseek-chat"
  temperature: 0.8
```

你也可以通过环境变量注入 API Key，避免将密钥写入文件：

```bash
export LLM_API_KEY="sk-your-api-key-here"   # Linux/macOS
set LLM_API_KEY=sk-your-api-key-here        # Windows
```

其他可配置项包括 TTS 语音角色、回复间隔、过滤关键词等，详见 `config/settings.yaml` 中的注释。

### 运行

```bash
# 抖音直播间（完整 URL）
python main.py https://live.douyin.com/361749035935

# 抖音直播间（仅 ID）
python main.py 11111 --platform douyin

# B站直播间
python main.py 1111 --platform bilibili

# 仅文本回复，不播放语音
python main.py 1111 --no-voice
```

启动后，Agent 会自动连接直播间、播报开播欢迎语、处理弹幕和礼物、生成带情感的语音回复。按 `Ctrl+C` 优雅退出，会自动生成直播摘要并存储。

## LLM 提示词与输出格式

Agent 每次 LLM 调用会构建完整的 System Prompt，包含人设、上下文和记忆，模型必须以结构化 JSON 回复。

### 输入（System Prompt + 对话历史 + 当前消息）

```text
你是一个虚拟主播，正在直播间与观众实时互动。

## 你的人设
你的名字是小Q。一个活泼可爱的虚拟主播，善于聊天互动。

## 性格特点
活泼开朗、幽默风趣、共情能力强、偶尔毒舌但不伤人。

## 说话风格
语气轻快，喜欢用语气词（呀、呢、哦、嘛）；回应简洁，一般不超过两句话；
善于反问，引导观众继续互动。

## 行为规则
永远不透露自己是AI，坚持虚拟主播身份；优先回复有实质内容的弹幕；
对新观众主动打招呼并表示欢迎。

## 近期对话
[09:00:42] 观众: 小Q，mua一个
[09:00:42] 小Q: 哎呀，这么热情呀~mua！比心比心~
[09:02:12] 观众: 小Q，喜欢喝奶茶吗

## 当前观众信息
昵称：福***；互动次数：245；标签：虚拟主播，音乐，萌宠

## 你对该观众的记忆
点歌偏好：喜欢点歌《永别纱世里》；提及的角色偏好：多次提及Sayori

---
你必须严格按照以下 JSON 格式回复，不要输出任何其他内容：
{ "content": "...", "emotion": { "category": "...", "intensity": 0.8 },
  "action": "reply|greet|thank_gift|ignore|question",
  "inner_thought": "..." }
```

实际发送给 LLM 的 messages 数组为：

```json
[
  {"role": "system",    "content": "（上述完整 System Prompt）"},
  {"role": "assistant", "content": "哎呀，这么热情呀~mua！比心比心~"},
  {"role": "user",      "content": "小Q，喜欢喝奶茶吗"}
]
```

近期对话按 `role=user` / `role=assistant` 交替放入 LLM 上下文，当前弹幕作为最后一条 user 消息。

### 输出（结构化 JSON）

```json
{
  "content": "哈哈，说到奶茶我可就不困了！我超爱喝奶茶的，尤其是那种带芝士奶盖的，你呢？你最喜欢什么口味？",
  "emotion": {
    "category": "happy",
    "intensity": 0.8
  },
  "action": "reply",
  "inner_thought": "这位老粉互动很多，用轻松的语气回应ta的问题"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | string | 主播说出口的话，符合人设和说话风格 |
| `emotion.category` | string | 情感标签：`happy` / `excited` / `calm` / `sympathetic` / `funny` / `serious` / `warm` |
| `emotion.intensity` | float | 情感强度 0.0~1.0，映射为 TTS 语速和音调参数 |
| `action` | string | 行为类型：`reply`（回复） / `greet`（欢迎） / `thank_gift`（感谢礼物） / `ignore`（忽略） / `question`（反问） |
| `inner_thought` | string | 内心独白，用于日志记录，不会被播出 |

每条弹幕经过 Agent Brain 处理后，LLM 的完整输入输出都会打印到日志（以 `─ LLM 输入 ─` 和 `─ LLM 输出 ─` 分隔线标识）。

## 扩展开发

### 添加新平台

继承 `connectors/base.py` 中的 `BaseConnector`，实现 `connect`、`disconnect`、`send_message` 三个方法，通过 `self._emit(LiveEvent(...))` 发送事件即可。

### 添加新人设

在 `config/personas/` 下新建 YAML 文件（参考 `default.yaml`），然后在 `settings.yaml` 中切换：

```yaml
agent:
  persona: "my-new-persona"
```

支持运行时热切换：`brain.switch_persona("my-new-persona")`。

### 添加新 LLM

任何兼容 OpenAI SDK 格式的 API 均可直接使用，只需在 `settings.yaml` 中修改 `base_url` 和 `model`。如需特殊处理，可基于 `llm/base.py` 的 `BaseLLMAdapter` 实现自定义适配器。

## 语音输出说明

TTS 引擎使用 Microsoft Edge TTS（免费、中文效果好），通过纯文本 + 语速/音调参数合成带情感的语音。生成的音频在本机播放。如果需要将语音推送到直播间，可以使用虚拟音频设备（如 VB-Cable）将播放输出作为麦克风输入。在 `config/settings.yaml` 中设置 `tts.auto_play: false` 可仅生成音频文件而不播放。

## 依赖项

| 依赖 | 用途 |
|------|------|
| openai | LLM 统一接口（兼容 DeepSeek / GPT / GLM） |
| edge-tts | 免费中文 TTS，通过 rate/pitch 参数控制情感 |
| websocket-client | 抖音 WebSocket 弹幕连接 |
| protobuf | 抖音消息协议解码 |
| aiosqlite | 异步 SQLite 记忆存储 |
| aiohttp | 异步 HTTP 客户端 |
| loguru | 结构化日志 |
| PyYAML | 配置文件和人设解析 |

完整清单见 `requirements.txt`。

## 许可证

MIT License

---

## 数据存储

项目使用 SQLite 持久化数据，数据库文件位于 `data/agent_memory.db`，包含以下表：

| 表 | 说明 | 关键字段 |
|---|---|---|
| `users` | 用户档案 | `platform_id`（平台UID）+ `platform`（平台名）联合唯一，`nickname` 仅展示 |
| `messages` | 对话记录 | `session_id` 归属场次，`role`（user/assistant），`content`，`emotion`，`action` |
| `sessions` | 直播场次 | `room_id`，`platform`，起止时间，`message_count`，`summary` |
| `memories` | 长期记忆 | `user_id` + `key` 联合唯一，`value`，`importance`（权重） |



## 桌面启动（Windows）

项目根目录提供了桌面启动脚本，一键启动 B站 1111 直播间：

```batch
启动LiveStream-Agent.bat
```

脚本自动设置 UTF-8 编码、PYTHONPATH，并调用 conda 环境的 Python 入口。启动后弹出三个窗口：
- **控制台窗口**：实时日志（LLM 输入/输出、TTS 合成状态）
- **字幕悬浮窗**：AI 回复文字显示（先于语音）
- **弹幕滚动窗**：实时显示观众弹幕，持续堆积

按 `Ctrl+C` 优雅退出。

---

**注意**：本项目仅用于学习和研究目的。请遵守各直播平台的使用条款，合理使用。
