# LiveStream-Agent 架构审查报告

> 审查日期：2026-06-28  
> 审查范围：全项目 35+ 文件  
> 项目版本：当前开发版

---

## 一、项目概览

LiveStream-Agent 是一个虚拟主播 AI 助手，核心流程为：连接直播平台（抖音/B站）→ 接收弹幕/礼物/进房事件 → AI 决策（LLM）→ 情感语音播报（edge-tts）。项目采用模块化架构，主要分层如下：

| 层级 | 模块 | 职责 |
|------|------|------|
| 入口 | `main.py` | 参数解析、组件初始化、生命周期管理 |
| 连接层 | `connectors/` | 平台连接器（douyin、bilibili），WebSocket 通信 |
| 管道层 | `pipeline/` | 消息过滤、调度、编排（Orchestrator） |
| 业务层 | `agent/` | AI 大脑（Brain）、记忆系统（Memory）、情感引擎（Emotion）、人设管理（Persona） |
| 语音层 | `speech/` | edge-tts 合成、音频播放 |
| 展示层 | `ui/` | tkinter 桌面字幕叠加窗口 |
| LLM 层 | `llm/` | LLM 适配器（DeepSeek API / OpenAI 兼容） |
| 存储层 | `storage/` | SQLite（aiosqlite + WAL 模式） |
| 配置层 | `config/` | YAML 配置（settings、personas、prompts） |

---

## 二、严重问题（CRITICAL — 必须修复）

### C1. API Key 明文硬编码在配置文件中

**位置**：`config/settings.yaml`

**问题**：DeepSeek API Key 直接以明文形式写在 YAML 配置文件中，该文件会被提交到版本控制或意外泄露。

**风险**：API Key 泄露导致密钥滥用、账单损失、账户被封。

**建议**：
- 立即从 `settings.yaml` 中移除 API Key
- 改用环境变量（`os.environ.get("DEEPSEEK_API_KEY")`）或 `.env` 文件（`.gitignore` 排除）
- 在 `llm/adapter.py` 中设置读取优先级：环境变量 > .env > 配置文件默认值

### C2. 抖音 WebSocket 参数硬编码

**位置**：`connectors/douyin/connector.py`

**问题**：抖音直播间的 WebSocket 连接参数（`LIVE_SSR_DATA_ID`、签名算法等）硬编码在源码中。抖音频繁更新反爬策略，硬编码会使程序快速失效。

**风险**：抖音接口变更时需修改源码重新打包/部署。

**建议**：
- 将 WebSocket URL 模板、签名参数等提取到配置文件
- 考虑实现自动获取最新连接参数的机制（定期从抖音页面抓取 SSR 数据）

### C3. 缺少自动重连机制

**位置**：`connectors/` 全部连接器

**问题**：WebSocket 断开后不会自动重连，直播中断后必须手动重启程序。长时间直播场景下，网络波动、服务器维护等因素会导致频繁断连。

**风险**：直播过程中断连导致观众互动丢失，严重影响使用体验。

**建议**：
- 实现指数退避重连（初始 1s，最大 60s）
- 重连后自动恢复会话状态
- 添加最大重试次数限制（如 10 次）
- 在 `BaseConnector` 中实现通用重连逻辑，子类复用

### C4. 数据库单例并发问题

**位置**：`storage/database.py`

**问题**：`get_database()` 返回全局单例，但 `aiosqlite` 的连接在同一时间只能执行一个查询。如果 PipelineOrchestrator 和 MemoryManager 同时写入，可能触发 "database is locked"。

**风险**：数据库写入丢失，记忆系统数据不完整。

**建议**：
- 始终启用 WAL 模式（当前已启用，需确认）
- 为数据库操作添加重试逻辑（`_retry_on_locked` 装饰器）
- 或使用连接池（多连接模式）
- 设置合理的 `busy_timeout`

---

## 三、高优先级问题（HIGH）

### H1. 事件类型硬编码且缺少注册机制

**位置**：`pipeline/orchestrator.py` 的 `_process_event()` 方法

**问题**：事件类型（danmaku、gift、enter_room、like）通过 `if/elif` 硬编码分发。添加新事件类型（如 super_chat、follow）需要修改编排器源码，违反开闭原则。

**建议**：
- 实现事件处理器注册表（`dict[str, Callable]`）
- 连接器注册自己支持的事件类型和对应处理器
- 使编排器变成纯调度器，不耦合具体事件处理逻辑

### H2. B站连接器使用同步 WebSocket 库

**位置**：`connectors/bilibili/connector.py`

**问题**：`bilibili-api-python` 的 `LiveDanmaku` 使用同步 WebSocket，在 `connect()` 调用时会阻塞。目前通过 `asyncio.create_task()` 包装，但库内部仍会阻塞事件循环线程。

**风险**：当 WebSocket 阻塞时，整个 asyncio 事件循环被卡住，其他协程无法运行。

**建议**：
- 长期：寻找异步 B站直播库，或自己基于 `aiohttp`/`websockets` 实现
- 短期：使用 `loop.run_in_executor()` 将同步 WS 逻辑放入线程池，避免阻塞事件循环

### H3. TTS 合成阻塞事件循环

**位置**：`speech/tts.py`

**问题**：`edge-tts` 的 `Communicate.save()` 是异步的，但实际 I/O 操作（下载音频流）可能耗时较长（1-3 秒），这期间其他事件处理被阻塞。

**风险**：TTS 合成时弹幕处理延迟，多个弹幕快速到来时造成处理堆积。

**建议**：
- 使用专用 `asyncio.Queue` + worker 协程独立处理 TTS
- 实现"打断"机制：新消息到来时中断当前正在播放的语音
- 或使用线程池隔离 TTS 合成

### H4. 缺少 LLM 调用速率限制

**位置**：`agent/brain.py`

**问题**：没有对 LLM API 调用做速率限制。高峰期大量弹幕可能触发 API 限流，导致请求失败或账单飞涨。

**建议**：
- 在 `llm/adapter.py` 或 `agent/brain.py` 中添加令牌桶/滑动窗口限流
- 可配置参数：`max_calls_per_minute`、`max_calls_per_hour`
- 超限时自动排队或降级（使用缓存回复/预设话术）

### H5. 模块间隐式耦合

**位置**：`pipeline/orchestrator.py` 的 `__init__` 方法

**问题**：编排器通过构造函数接收 7+ 个依赖，且类型均为具体类而非接口。部分模块（如 `emotion.to_ssml()`）的使用方式隐式依赖其内部实现细节。

**建议**：
- 为核心接口定义 ABC 抽象基类或 Protocol
- 编排放器只依赖接口，不依赖具体实现
- 考虑引入依赖注入容器

### H6. 短期记忆仅存于内存

**位置**：`agent/memory.py` — `short_term_size` 控制的内存列表

**问题**：短期记忆完全存储在 Python 进程内存中，程序重启后丢失。长时间直播后重启，AI 将丢失上下文连贯性。

**建议**：
- 短期记忆也写入数据库（标记为 `is_short_term=True`）
- 启动时从数据库加载最近的 N 条记录作为短期记忆初始化
- 使用 LRU 策略控制内存中的缓存大小

---

## 四、中优先级问题（MODERATE）

### M1. TTS 语音名称重复配置

**位置**：`agent/emotion.py` 的 `to_ssml()` 和 `speech/tts.py`

**问题**：（已在本次审查中修复）之前在 SSML 中硬编码了 `<voice name="zh-CN-XiaoyiNeural">`，而 `tts.py` 也通过 `voice` 参数传入相同的值，造成重复。已移除 SSML 中的 voice 标签。

### M2. 情感识别依赖简单关键词匹配

**位置**：`agent/emotion.py` 的 `find_best_emotion()` 方法

**问题**：当 LLM 未返回情感标签时，降级策略是关键词匹配（"哈哈"→"funny"，"谢谢"→"warm"），覆盖场景有限且容易误判。

**建议**：
- 实现轻量级情感分类模型（如 `text2vec` 或 `sentence-transformers` 微调模型）
- 或扩展关键词库并加入否定逻辑（"不好笑" 不应匹配 "funny"）
- 作为中期改进，优先级不高

### M3. 音频播放器缺少后端缓存

**位置**：`speech/tts.py`

**问题**：每次播放都重新调用 edge-tts 合成音频。对于高频回复（如 "欢迎新朋友"、"谢谢礼物"），可以预合成并缓存，减少网络请求和延迟。

**建议**：
- 实现 LRU 缓存（如缓存最近 50 条语音的 mp3 文件）
- 缓存 key 使用 `(text, emotion, intensity)` 的哈希
- 设置缓存过期时间（如 1小时）

### M4. 缺少 LLM 降级策略

**位置**：`agent/brain.py`

**问题**：当 LLM API 不可用时（超时、限流、余额不足），程序没有降级方案，所有回复都会静默失败。

**建议**：
- 实现模板回复降级（基于事件类型的预设话术列表）
- 或使用本地轻量模型（如 Ollama）作为备选
- 降级时日志告警，但不中断服务

### M5. brain.py 中存在重复代码

**位置**：`agent/brain.py`

**问题**：`thank_gift()` 和 `greet()` 方法的回复处理逻辑高度相似（调用 LLM → 检查 should_reply → 返回），可以抽取公共方法。

**建议**：提取 `_generate_response(prompt_template, **kwargs)` 通用方法，消除重复。

### M6. 日志中可能泄露敏感信息

**位置**：多处 `logger.info()` 调用

**问题**：日志中可能输出完整用户消息内容、API 响应等。如果日志文件被分享或上传，可能泄露用户数据。

**建议**：
- 对用户消息日志做截断处理（已部分实现，`content[:60]`）
- 实现日志脱敏装饰器
- API 响应不直接输出到日志

---

## 五、低优先级问题（LOW）

### L1. 连接器导入路径使用 sys.path 操作

**位置**：`connectors/douyin/connector.py`

**问题**：通过 `sys.path.insert(0, ...)` 添加 protobuf 生成文件的导入路径，这是脆弱的工作区。

**建议**：将 protobuf 生成文件放到正式的包目录下，或使用相对导入。

### L2. 魔法数字散落各处

**位置**：多处

**问题**：`maxsize=100`、`timeout=1.0`、`maxsize=32`、`after(100)` 等硬编码数字缺少常量定义和注释说明。

**建议**：提取到模块顶部做命名常量，或放入配置文件。

### L3. 缺少类型注解

**位置**：多处函数参数和返回值

**问题**：`brain`、`emotion`、`tts` 等参数在编排器中无类型注解，IDE 无法提供自动补全和类型检查。

**建议**：逐步补充类型注解，至少为公开 API 方法添加。

### L4. 测试目录为空

**位置**：`tests/` 目录

**问题**：项目包含 `tests/` 目录但没有任何测试文件。无单元测试或集成测试覆盖。

**建议**：
- 至少为核心模块添加单元测试（MemoryManager、EmotionEngine、MessageFilter）
- 使用 `pytest` + `pytest-asyncio` 框架
- 优先覆盖容易出错的模块（情感引擎的 SSML 生成、消息过滤器）

### L5. requirements.txt 不完整

**位置**：`requirements.txt`

**问题**：依赖列表可能不完整，某些依赖是在开发过程中手动安装的（如 bilibili-api-python、protobuf）。

**建议**：使用 `pip freeze` 生成完整依赖列表，区分核心依赖和开发依赖。

### L6. README 链接可能失效

**位置**：`README.md`

**问题**：README 中的外部链接（如 protobuf 编译工具下载链接）可能已失效或需要翻墙。

**建议**：检查所有外部链接的有效性，或将关键工具提供本地镜像/备用链接。

---

## 六、架构优势

在指出问题的同时，以下架构决策值得肯定：

1. **管道模式**：PipelineOrchestrator 串联各模块，数据流清晰，每个阶段职责单一
2. **配置驱动**：YAML 多层配置，personas/prompts 可热切换，无需修改代码
3. **适配器模式**：LLM 和 TTS 都使用适配器，更换底层实现不影响上层
4. **ABC 基类**：BaseConnector 定义了连接器接口，方便扩展新平台
5. **线程安全**：WebSocket 线程 → asyncio 队列的桥接设计合理
6. **三级记忆**：短期/长期/会话三级记忆架构设计前瞻
7. **优雅退出**：finally 块中完整的资源清理流程
8. **结构化日志**：使用 loguru 支持按大小/时间轮转

---

## 七、改进优先级路线图

### 第一阶段（立即 — 上线前必须完成）
- [ ] C1: 移除硬编码 API Key，改用环境变量
- [ ] C3: 实现自动重连机制
- [ ] C4: 数据库写入重试逻辑

### 第二阶段（短期 — 1-2周内）
- [ ] H1: 事件处理器注册机制
- [ ] H4: LLM 调用速率限制
- [ ] H6: 短期记忆持久化
- [ ] M4: LLM 降级策略

### 第三阶段（中期 — 1个月内）
- [ ] H2: B站连接器异步改造
- [ ] H3: TTS 合成独立 worker
- [ ] M3: 语音缓存
- [ ] M2: 情感识别增强

### 第四阶段（长期 — 持续改进）
- [ ] L4: 测试覆盖
- [ ] M5: 代码去重
- [ ] L3: 类型注解
- [ ] L1-L2: 代码规范化

---

*报告由 QoderWork 自动生成，基于对项目 35+ 源文件的静态分析。*
