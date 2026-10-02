# Mira 设计文档

- 日期：2026-10-02
- 状态：待用户审阅
- 范围：第一版（v1）

## 1. 目标

Mira 是一个只给用户本人使用的 AI 朋友，有长期记忆。

**定位**：坦诚自己是 AI 的朋友，不编造自己的生活经历，但有稳定的性格、观点和说话方式。她聪明、理性、温柔，是一个**诤友**：会在你做得不好的地方指出问题，但会看时机。

**成功标准**：使用几周后，用户感觉 Mira 真的记得自己，能自然地提起过去的事，注意到反复出现的模式，并在合适的时机温和而具体地指出来。

### 1.1 用户明确的需求

| # | 需求 |
|---|---|
| R1 | 单用户，自己用 |
| R2 | 人设：聪明、理性、温柔的诤友；坦诚是 AI |
| R3 | 指出问题要看时机：情绪差时先陪伴、不说教；状态合适时再提，且要具体、有证据 |
| R4 | 重点关注两个领域：① 目标与行动（承诺是否兑现、拖延、计划与实际的落差）② 情绪与思维模式 |
| R5 | 入口：本机网页（localhost），主要在电脑上使用 |
| R6 | 模型全用 DeepSeek：聊天用 V4 Pro，后台任务用 Flash（反思器例外，见 §5.5） |
| R7 | 用户连发多条消息时，Mira 等用户说完再回 |
| R8 | Mira 的回复拆成多条气泡，逐条发送 |
| R9 | 有记忆管理页，可以浏览、搜索、修改、删除记忆 |
| R10 | v1 只被动回复。主动消息只有在"聪明"时才值得做（不要定时问候），留到以后 |

### 1.2 设计者做的决定（已获用户认可）

- 技术栈：Python 3.12 + FastAPI + SQLite + 不需要构建的 HTML/JS 前端（用户是编程新手，选择 AI 最容易维护、依赖最少的方案）
- 单条连续对话，像和一个人的微信聊天窗口，不做多会话
- v1 就记录"承诺/待跟进事项"，支撑 R4，也为以后的主动消息做准备
- 危机信号要特殊处理
- 人设放在可以编辑的 `persona.md` 文件里

### 1.3 v1 不做

主动消息、iMessage/Telegram 等其他入口、语音、图片收发、手机访问、多用户、人设编辑界面。

## 2. 调研结论

与原始构想（"检索记忆 → 拼上下文 → LLM → 提取值得保存的信息 → 写回"）类似的开源项目很多，这已经是陪伴类应用的标准架构：

- 完整应用：MaiBot（麦麦）、KouriChat、AIRI、Open-LLM-VTuber、AstrBot + proactive_chat 插件
- 记忆层：Mem0（逐轮提取事实并合并，和本设计的写入器最接近）、Letta/MemGPT（由模型通过工具调用编辑记忆）、Zep（时间知识图谱）
- 值得借鉴的思路：Ombre-Brain（记忆带情绪标签）、Paramecium（原始聊天记录是唯一事实来源，向量只是索引）
- 合集：github.com/DasterProkio/awesome-ai-companion、github.com/Eibon47/awesome-ai-companion

**不直接用 Mem0 的原因**：它擅长"事实"，但不擅长"模式（附证据）"和"承诺跟进"，而这两者正是 R4 的核心；而且出问题时新手很难调试一个外部库的内部逻辑。

**与原始流程图的不同**：去掉"判断这句话是否涉及过去记忆"这一步，改成**每轮都检索**。本地检索只需几十毫秒；如果用模型来判断，每轮会多一次调用，而且容易漏判（例如"今天又没去"单看这句判断不出和记忆有关）。

## 3. 架构

```
┌──────────── 浏览器（127.0.0.1:8000） ────────────┐
│     聊天页 /                    记忆管理页 /memory  │
└──────┬─────────────────────────────────┬─────────┘
       │ WebSocket /ws                     │ HTTP /api/...
┌──────▼─────────────────────────────────▼─────────┐
│                  FastAPI 后端                       │
│  chat（对话编排） → retriever / llm / store          │
│  writer（记忆写入）    reflector（每日反思）           │
│  scheduler（空闲计时 + 每日任务 + 启动补跑）           │
└──────┬────────────────────────────────────────────┘
   data/mira.db    persona.md    .env
```

### 3.1 模块

| 模块 | 职责 | 依赖 |
|---|---|---|
| `config` | 读取 `.env` 和默认配置（模型名、各种时间参数、热线号码） | 无 |
| `llm` | 封装 DeepSeek 调用（OpenAI 兼容 SDK，设置 `base_url`），提供 `chat_model` 和 `background_model` 两种用途；支持 JSON 输出、重试 | DeepSeek API |
| `store` | **唯一**读写 SQLite 的模块：消息、记忆、向量、核心档案、日志 | SQLite |
| `embedder` | 加载本地 embedding 模型，把文本转成向量 | fastembed + `BAAI/bge-small-zh-v1.5` |
| `retriever` | 输入一段查询文本，返回打分最高的若干条记忆 | store、embedder |
| `writer` | 输入一段未处理的对话，生成记忆操作，校验后执行 | llm、retriever、store |
| `reflector` | 回顾近期内容，更新模式、承诺状态和核心档案 | llm、store |
| `chat` | 对话编排：等待用户说完 → 检索 → 组装提示词 → 调模型 → 分条发送 → 处理被打断 | 以上所有 |
| `scheduler` | 后台任务：空闲 10 分钟触发 writer；每天 04:00 触发 reflector；启动时补跑遗漏的任务 | writer、reflector |
| `web` | 静态文件：`index.html`（聊天）、`memory.html`（记忆管理） | 后端 API |

> 注：`fastembed` 和 `bge-small-zh-v1.5` 是否适合，需要在实施计划的第一步实际验证（安装大小、中文效果、Apple Silicon 兼容性）。如果不合适，退回 `sentence-transformers`。

### 3.2 配置项（默认值）

| 键 | 默认值 |
|---|---|
| `DEEPSEEK_API_KEY` | （必填，放在 `.env`） |
| `CHAT_MODEL` | `deepseek-v4-pro` |
| `BACKGROUND_MODEL` | `deepseek-flash` |
| `REFLECT_MODEL` | `deepseek-v4-pro` |
| `DEBOUNCE_SECONDS` | 3 |
| `MAX_WAIT_SECONDS` | 60 |
| `IDLE_WRITE_MINUTES` | 10 |
| `REFLECT_HOUR` | 4 |
| `RECENT_HISTORY_TOKENS` | 4000 |
| `RETRIEVE_TOP_K` | 8 |
| `CRISIS_RESOURCES` | 全国心理援助热线 12356；希望24热线 400-161-9995；紧急情况 120/110 |

## 4. 对话流程

1. **收到消息**：前端通过 WebSocket 发送 `{type:"message", text}`，后端**立即存储**。用户在输入框打字时，前端每 2 秒发一次 `{type:"typing"}`。
2. **等待用户说完**：用户最后一条消息发出 `DEBOUNCE_SECONDS` 秒后，如果期间没有收到 `typing`，就开始处理；否则继续等，从第一条消息算起最多等 `MAX_WAIT_SECONDS` 秒。期间收到的所有消息合并成一批（同一个 `batch_id`）。
3. **检索**：查询文本 = 这批新消息 + 最近 3 轮对话。取回 `RETRIEVE_TOP_K` 条记忆，外加**所有**状态为进行中或逾期的 `commitment`。
4. **组装上下文**（按顺序排列，让 DeepSeek 的前缀缓存尽量命中）：
   1. system：`persona.md` + 行为规则（§4.1）
   2. system：当前核心档案
   3. 最近的聊天记录（按 token 截取，约 `RECENT_HISTORY_TOKENS`）
   4. system：本轮检索到的记忆 + 进行中的承诺 + 当前时间 + 距上次聊天多久
   5. user：本批新消息
5. **调用 `CHAT_MODEL`**，使用 JSON 输出模式：
   ```json
   {"mood_read": "string", "approach": "comfort|normal|raise_issue|crisis", "messages": ["string", "..."]}
   ```
   `messages` 一般 1–4 条。`mood_read` 和 `approach` 存进消息的 `meta` 字段，不展示给用户。
6. **分条发送**：每条之前先发 `{type:"typing"}`，等待 `min(0.6 + 0.05 × 字数, 4)` 秒，再发 `{type:"bubble", text}`。每发出一条就存一条。
7. **被打断**：发送过程中收到用户的新消息时，取消剩余的气泡（不存储），新消息进入下一批，回到第 2 步。
8. **收尾**：重置空闲计时器。

### 4.1 行为规则（写在系统提示词里，要点如下）

- 坦诚自己是 AI，不编造个人经历。
- `comfort`：只陪伴和共情，不分析、不给建议、不指出问题。
- `raise_issue`：只在用户状态平稳时使用；必须引用具体证据（记忆中的承诺、模式的证据），语气温和、直接，提一个点就够，不要列清单。
- `crisis`：认真对待，陪伴，给出 `CRISIS_RESOURCES`，鼓励用户联系身边的人或专业人士。
- 持续低落的模式出现时，可以在合适的时机建议用户寻求专业帮助。
- 不确定的记忆用提问的方式求证（"我记得你好像说过……是吗？"），绝不编造。
- 像聊天一样说话：短句、分条、口语化，不用 markdown，不写长篇大论。
- 自然地利用时间信息（例如隔了几天没聊、深夜还在聊）。

## 5. 记忆系统

### 5.1 数据表

```sql
messages(id, role, content, batch_id, created_at, meta_json, processed)  -- processed：是否已被 writer 处理
memories(id, type, content, subject, importance, status, due_at,
         evidence_json, source_message_ids_json, user_locked,
         superseded_by, created_at, updated_at, last_recalled_at)
memory_vectors(memory_id, vector BLOB)            -- 512 维 float32
memories_fts  -- FTS5，trigram 分词器（适用于中文），索引 memories.content
core_profile(id, content, created_at, source)     -- source: reflector | user
memory_log(id, memory_id, actor, op, before_json, after_json, created_at)  -- actor: writer | reflector | user
job_state(name, last_run_at)                      -- 用于判断启动时是否需要补跑
```

### 5.2 记忆类型

| type | 含义 | 使用的字段 |
|---|---|---|
| `fact` | 关于用户的事实或偏好 | importance |
| `person` | 用户生活中的人 | subject（人名）、importance |
| `commitment` | 承诺或待跟进的事 | status（open/done/dropped/overdue）、due_at |
| `pattern` | 反复出现的行为、情绪或思维模式 | evidence_json（指向 episode 的 id 列表，至少 2 条） |
| `episode` | 一段对话的事件摘要 | 情绪基调写在 content 里 |

信息过时（例如换了工作）时，不删除旧记忆，而是设置 `superseded_by` 指向新记忆。被替代的记忆不参与检索，但在管理页可以看到。

### 5.3 检索打分

候选范围：所有 `superseded_by IS NULL` 的记忆。其中 episode 有额外限制：最近 90 天内的全部参与打分；90 天以前的，只有语义相似度排进前 20 的才参与打分（避免大量旧的事件摘要稀释检索结果）。

```
score = 0.6 × cosine(查询, 记忆) + 0.25 × FTS 匹配（归一化） + 0.15 × (0.5 × importance/5 + 0.5 × 新近度)
新近度 = exp(-距今天数 / 30)
```

所有向量在启动时加载到内存，用 numpy 直接全部计算。被选中的记忆更新 `last_recalled_at`。

### 5.4 写入器

- **触发时机**：空闲 `IDLE_WRITE_MINUTES` 分钟；启动时发现有未处理的消息。
- **输入**：所有 `processed=0` 的消息 + 与这段对话相关的已有记忆（用检索器，取约 15 条）+ 所有进行中的 commitment。
- **输出**（`BACKGROUND_MODEL`，JSON 模式）：
  ```json
  {"ops": [
     {"op": "add", "type": "...", "content": "...", "subject": "...", "importance": 3, "due_at": null},
     {"op": "update", "id": 12, "content": "..."},
     {"op": "supersede", "id": 12, "new": {...}},
     {"op": "set_status", "id": 30, "status": "done"}
   ],
   "episode": {"content": "...", "importance": 2}}
  ```
- **校验**：id 必须存在；type 和 status 必须是合法值；`user_locked=1` 的记忆不允许 update 或 supersede；不合法的操作跳过并记录日志。
- **执行**：放在一个数据库事务里：执行操作 → 写入 episode → 生成向量 → 写入 `memory_log` → 把消息标记为 `processed=1`。任何一步失败就整体回滚，下次再处理。
- **记什么**：用户生活中的事实、提到的人、带时间的打算和承诺、有情绪起伏的事件。**不记**：寒暄、Mira 自己说的话、明显一时的状态。

### 5.5 反思器

- **触发时机**：每天 `REFLECT_HOUR` 点；启动时，如果距离上次运行超过 24 小时就补跑。
- **模型**：`REFLECT_MODEL`（V4 Pro）。每天只跑一次，费用可以忽略，而模式识别的质量很重要，所以不用 Flash（这是对 R6 的唯一例外）。
- **输入**：最近 7 天的 episode、所有 pattern、所有进行中或逾期的 commitment、当前核心档案。
- **输出**：
  - pattern 操作：新增（必须附上 ≥2 条 episode 证据）/ 给已有模式追加证据 / 合并重复的模式
  - commitment：超过 due_at 仍未完成的标为 `overdue`
  - 新版核心档案（≤ 2500 token）：用户是谁、近况、重要的人、正在追踪的目标、观察到的模式
- 校验、事务、日志的规则同写入器。新核心档案作为新的一行插入，不覆盖旧版本。

## 6. 网页界面

### 6.1 聊天页 `/`
- 单条连续对话；向上滚动分页加载历史；按日期分隔
- Mira 的气泡在左，用户的在右；"输入中"显示为三个跳动的点
- Enter 发送，Shift+Enter 换行
- 出错时显示灰色系统提示 + 重试按钮（重新处理最后一批消息）
- 跟随系统的深色/浅色模式
- 右上角有入口进入记忆管理页

### 6.2 记忆管理页 `/memory`
- 标签页：核心档案 · 承诺 · 模式 · 人物 · 事实 · 事件
- 搜索框：调用 retriever（搜到的内容就是 Mira 能想起来的）
- 记忆卡片：内容、时间、重要度、"来源"（展开显示原始对话片段）、编辑（编辑后自动 `user_locked=1`）、删除
- 承诺卡片：状态切换；模式卡片：列出证据，每条证据可以展开
- 核心档案：查看、编辑（存为 `source=user` 的新版本）、版本历史、回滚
- 顶部"最近她记住了什么"：最近 20 条 `memory_log`

### 6.3 HTTP API

```
GET    /api/messages?before=<id>&limit=50
GET    /api/memories?type=&q=
GET    /api/memories/{id}            # 包括来源消息
PATCH  /api/memories/{id}            # 修改内容/状态，自动加锁
DELETE /api/memories/{id}            # 删除（同时写入 memory_log）
GET    /api/profile                  # 当前核心档案 + 历史版本
PUT    /api/profile                  # 用户编辑后存为新版本
POST   /api/profile/rollback/{id}
GET    /api/memory-log?limit=20
WS     /ws                           # 收：message / typing；发：typing / bubble / error
```

## 7. 安全与隐私

- 服务只绑定 `127.0.0.1`（没有登录功能，所以不能让局域网访问）。
- 数据只发往 DeepSeek API。`.env` 不纳入版本管理。备份方法是复制 `data/mira.db`。
- 危机处理见 §4.1；热线号码可以在配置里修改。

## 8. 出错处理

| 情况 | 处理 |
|---|---|
| 聊天调用失败 | 指数退避重试 2 次；仍失败就通过 WS 发 `error`，前端显示重试按钮；用户消息已经存储，不会丢 |
| 聊天返回的 JSON 不合法 | 重试 1 次；仍不合法，就把原文当成一条 bubble 发出，`approach` 记为 `unknown` |
| 写入器 / 反思器失败 | 事务回滚，下次触发时重试；记录日志 |
| 模型给出非法的记忆操作 | 跳过该操作并记录日志，其余合法操作照常执行 |
| embedding 模型下载失败 | 启动时打印中文说明，并退出 |
| 缺少 API key | 启动时打印中文说明（如何创建 `.env`），并退出 |

## 9. 测试

1. **单元测试**（pytest，使用假的 LLM，不花钱）：store 读写、检索打分公式、等待计时（模拟时钟）、分条发送和被打断、写入器操作校验（非法 id、修改已锁定记忆、类型非法）、反思器的"≥2 条证据"规则、启动补跑判断。
2. **记忆质量评估**（调用真实 API，跑一次几分钱）：`evals/` 目录下约 10 段剧本对话，每段写明期望结果，例如：
   - "周五下午面试" → 生成 commitment，due_at 正确
   - 两次"压力大熬夜"的 episode → 反思器生成 pattern，附 2 条证据
   - 纯寒暄 → 只生成 episode，不生成其他记忆
   - "我换工作了，现在在 B 公司" → 原 fact 被 supersede
3. **真实使用**：用户使用一周，在记忆管理页检查准确性。

## 10. 目录结构

```
Mira/
├── README.md              # 中文的安装、启动、备份说明
├── pyproject.toml
├── .env.example
├── persona.md
├── mira/
│   ├── main.py            # FastAPI 入口，挂载路由、WS、静态文件，启动 scheduler
│   ├── config.py
│   ├── llm.py
│   ├── store.py
│   ├── embedder.py
│   ├── retriever.py
│   ├── writer.py
│   ├── reflector.py
│   ├── chat.py
│   ├── scheduler.py
│   └── prompts/           # 各个提示词模板（chat_rules / writer / reflector）
├── web/
│   ├── index.html
│   ├── memory.html
│   └── *.js / *.css
├── data/                  # mira.db（不纳入版本管理）
├── evals/
└── tests/
```

## 11. 以后可以做的扩展（v1 不实现，但设计上预留了）

- **聪明的主动消息**：scheduler 定期运行，读取到期或逾期的 commitment 和距上次聊天的时间，由模型判断"现在值不值得开口"，再通过浏览器通知发送。
- **其他入口**：`chat` 模块与 WebSocket 解耦，以后可以加 Telegram 或 iMessage 适配器。
- **换模型**：`llm` 使用 OpenAI 兼容接口，换模型只需要修改配置。
