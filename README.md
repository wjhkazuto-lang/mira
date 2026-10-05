# Mira

> A personal AI friend with long-term memory that runs on your own computer. Mira remembers what you tell her, notices recurring patterns in your goals and moods, and — like a good friend — gently points out problems at the right moment. Chinese-first UI; uses the DeepSeek API; all data stays in a local SQLite file. MIT licensed. Built with the help of Claude (Anthropic).

Mira 是一个运行在你自己电脑上的 AI 朋友。她会记得你说过的话，留意你反复出现的模式，在合适的时候温和地指出你的问题。

这个项目由作者和 [Claude](https://www.anthropic.com/claude)（Anthropic 的 AI）一起完成：作者提出想法、做决定、把关体验，设计、代码和测试大部分由 Claude 在作者的指导下编写。

## 她能做什么

- **长期记忆**：聊完后，她会自动整理值得记住的东西，分成事实、人物、想法、目标、承诺、模式、事件几类
- **分得清想法和承诺**：随口一说的"想法"她只记住、不追问；长期在做的"目标"偶尔关心；说定了时间的"承诺"才会跟进和提醒。"我坚持不住"这类一时的自我否定不会被记成关于你的事实
- **诤友**：聪明、理性、温柔。你情绪不好时，她先陪着你；你状态好的时候，她会有理有据地指出问题（比如"你上周说周五前改完简历的"）
- **每日反思**：每天回顾一次，发现你反复出现的行为和情绪模式，更新她对你的整体了解（核心档案）
- **主动关心**（默认开启）：你很久没说话、或说好的事情到期了，她会在值得的时候自己先开口——打开 App 时可能看到她已经说了一句；如果 Mira 开着但窗口在后台，她开口时 Dock 图标会连跳几秒、点上一个圆点（回到窗口或你开口，圆点就消失；系统通知需要签名 App，发不了）。不会定时问候，没什么可说的就保持安静；每天最多一条、安静时段（默认 22:00–08:00）不打扰。另外，**每周日晚上（默认 20:00，可以在 `.env` 里改）会收到一封"每周信"**：回顾这周发生的事、带一句下周的关心或提醒。可以用 `.env` 里的 `PROACTIVE=0` 整体关掉、`NOTIFICATIONS=0` 只关 Dock 提醒
- **像真人一样聊天**：等你连着发完几条再回；回复拆成几条，一条一条地发
- **记忆透明**：在记忆管理页能看到她记住的所有东西，可以修改、删除，也能看到每条记忆是从哪段对话来的

## 隐私

- 聊天记录和记忆**全部存在你电脑上**的 `data/mira.db` 文件里
- 服务只监听 `127.0.0.1`，同一网络下的其他设备访问不到；也会拒绝其他网站在你浏览器里发起的访问
- **没有登录密码**：这台电脑上的其他程序仍然可以访问 Mira 的接口、读写聊天和记忆。请只在自己信任的电脑上使用
- 每次聊天和整理记忆时，相关内容会发送给 **DeepSeek API** 处理，请了解这一点后再使用

## 免责声明

Mira 是一个陪伴型的聊天程序，**不能替代专业的心理咨询或医疗帮助**。如果你正处在危机中，请立即联系身边信任的人，或拨打求助热线。默认配置中的热线是中国大陆的号码（全国心理援助热线 12356、希望24热线 400-161-9995、紧急情况 120/110），其他地区请在 `.env` 的 `CRISIS_RESOURCES` 中改成当地的号码。

## 安装

需要 macOS / Linux（Windows 未测试）。

1. 安装 [uv](https://docs.astral.sh/uv/)（Python 环境管理工具）：
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. 下载本项目，在项目目录里安装依赖（uv 会自动安装 Python 3.12）：
   ```bash
   uv sync
   ```
3. 复制配置文件，填入你的 [DeepSeek API key](https://platform.deepseek.com)：
   ```bash
   cp .env.example .env
   ```
   用任意文本编辑器打开 `.env`，在 `DEEPSEEK_API_KEY=` 后面填上 key。其他配置项都有中文说明，一般不用改。

## 启动

```bash
uv run python -m mira
```

然后在浏览器打开 <http://127.0.0.1:8000>。第一次启动时会下载一个约 90MB 的中文向量模型（用于检索记忆），之后就不用再下载了。

### 用 App 打开（可选）

不想每次都开终端，可以生成一个 Mira.app：

```bash
uv run python -m mira.make_app
```

它会在你个人文件夹里的"应用程序"（`~/Applications`）里放一个 Mira，生成后会自动在 Finder 里选中它。注意这不是 Finder 侧边栏里那个"应用程序"：以后要找它，可以在 Finder 里按 ⇧⌘H 打开个人文件夹再进"应用程序"，或者用聚焦搜索（⌘空格）搜"Mira"。双击打开，Mira 会在自己的窗口里运行，可以把它拖进 Dock。如果项目里有 `theme/mira/avatar.png`，会用它做图标。

- **关窗口（或按 ⌘Q）就会停止 Mira**。如果你已经在终端里启动了 Mira，App 只会打开一个窗口，关掉窗口不会停止终端里的那个
- 日志在 `data/logs/mira.log`，打不开时可以看看这里
- 这个 App 只能在这台 Mac 上用：它指向当前的项目文件夹，并且需要装有 uv。**移动或改名项目文件夹后，要重新运行一次上面的命令**
- 如果个人文件夹的"应用程序"里已有不是这样生成的 Mira.app，命令会拒绝覆盖它（把它改名或移到废纸篓后再运行一次）
- 上面的终端启动方式仍然可用

### 只想先看看界面？

开发模式下用的是假回复，不需要 API key，也不会下载模型：

```bash
MIRA_FAKE=1 DB_PATH=data/dev.db uv run python -m mira
```

## 日常使用

- **备份**：Mira 每天自动备份一次到 `data/backups/`，默认保留最近 14 份；位置和份数可以在 `.env` 里用 `BACKUP_DIR`、`BACKUP_KEEP` 修改。记忆页上也能看到"上次备份"，点"立即备份"可以马上备一份（文件名带 `-manual-`，单独计数，最多留最近 5 份，不会挤掉自动备份）
- **恢复备份**：
  1. 先退出 Mira：关掉 App 窗口；如果是在终端里启动的，要在那个终端按 Ctrl+C（关 App 窗口停不掉终端里的 Mira）
  2. 把现在的 `data/mira.db` 改名为 `mira.db.old`（先留着，别直接覆盖）
  3. 如果 `data/` 里有 `mira.db-wal`、`mira.db-shm` 这两个文件，把它们删掉——一定要删，否则恢复不会生效
  4. 把 `data/backups/` 里想要的那个备份（文件名像 `mira-20261004-153000.db`；点"立即备份"留下的 `mira-manual-…db` 也一样）复制到 `data/`，改名为 `mira.db`
  5. 重新启动 Mira
- **选人设 / 改人设**：见下面的"人设"一节
- **二次元界面**：在项目根目录建一个 `theme/` 文件夹（不会被 git 提交）：
  - `theme/mira/` 放 Mira 的立绘，每种表情一张，文件名用 `calm`（平静）、`talk`（说话）、`smile`（微笑）、`happy`（开心）、`gentle`（温柔）、`worried`（担心）、`surprised`（惊讶）、`annoyed`（不满），格式 png/webp/jpg 都行，有几张放几张。她会按每次回复的语气换表情
  - `theme/background/` 放背景，文件名 `day` / `dusk` / `night`，按你电脑的时间切换；只放一个就全天都用它。可以是图片、动图（gif）或视频（mp4 / webm），视频会静音循环，窗口不在前台时自动暂停
  - 免费立绘可以去 [わたおきば](https://wataokiba.net/) 之类的素材站找；大多数素材**禁止二次分发**，所以只放在本地，别上传
- **觉得她说话像机器人**：运行 `uv run python evals/style_stats.py --since 某天` 看看她回复的统计（每轮几条、多少以问句结尾），也可以在 `.env` 里调 `CHAT_TEMPERATURE`
- **纠正记忆**：在记忆管理页（右上角"记忆"）修改或删除。你改过的记忆会被锁定，她不会再自动改动
- **整理进度**：记忆页上方显示待处理消息数、预计整理时间、上次成功时间及失败提示。你发消息和 Mira 回复都会顺延空闲计时；后台每约 30 秒检查一次，模型处理还需要额外时间。失败后等待约 30 分钟重试。
- **自动刷新**：记忆页在前台时每 5 秒更新，编辑内容或查看展开的来源时暂停刷新列表，状态仍会更新。上次成功时间保存在本地；正在执行和失败待重试状态仅对应当前运行进程，重启后重新判断并补处理。整理成功不保证一定产生新记忆。

## 人设

Mira 的性格写在一份 Markdown 文件里，`personas/` 里有 5 个现成的模板：

| 模板 | 风格 |
|---|---|
| `personas/zhengyou.md`（默认） | **诤友**：聪明、理性、温柔，看时机指出你的问题 |
| `personas/listener.md` | **温柔倾听者**：以陪伴和倾听为主，你问了才给建议 |
| `personas/sunyou.md` | **损友**：爱吐槽、会玩梗，关键时刻很靠谱 |
| `personas/study-buddy.md` | **自律搭子**：关心你的目标和进度，帮你拆小步、适度督促 |
| `personas/genki.md` | **元气伙伴**：活泼热情、很会捧场 |

**三种用法**（改完重启 Mira 生效）：

1. **直接换模板**：在 `.env` 里写 `PERSONA_PATH=personas/sunyou.md`
2. **在模板上改**：把喜欢的模板复制一份，命名为项目根目录下的 `persona.local.md`，随便改。它会被优先使用，**而且不会被 git 提交**——在"关于 TA"一节写写你自己，她会更懂你
3. **自己从头写**：建议沿用模板的结构——性格、说话风格、关于 TA、相处方式、示范台词。也可以让你常用的 AI（比如 ChatGPT）根据对你的了解来写，提示词见 [docs/persona-prompt-for-gpt.md](docs/persona-prompt-for-gpt.md)

写人设时只写"她是谁、怎么和你相处"。回复格式、什么时候安慰、什么时候指出问题、危机时怎么做，这些规则程序里已经统一处理，人设里不用写，写了反而可能冲突。

## 费用

用默认配置（聊天用 DeepSeek V4 Pro，后台整理用 Flash），每天聊 50 条左右，每月大约 $4–7。

## 开发

```bash
uv run pytest                          # 单元测试（不调用 API，不花钱）
node --test tests/web/sync.test.js     # 前端消息合并逻辑的测试（需要 Node.js）
RUN_SLOW=1 uv run pytest -m slow       # 测试真实的向量模型（需要联网下载）
uv run python evals/run_evals.py       # 记忆质量评估（调用真实 API，跑一次约几分钱）
```

设计文档在 `docs/superpowers/specs/`，实施计划在 `docs/superpowers/plans/`。

代码结构：

| 文件 | 作用 |
|---|---|
| `mira/chat.py` | 对话编排：等你说完 → 检索记忆 → 调模型 → 分条发送 |
| `mira/writer.py` | 聊天空闲后，从对话中提取记忆 |
| `mira/reflector.py` | 每日反思：发现模式、更新核心档案、标记逾期承诺 |
| `mira/proactive.py` | 主动关心：判断值不值得开口，值得才调模型说一句 |
| `mira/retriever.py` | 混合检索（语义 + 关键词 + 重要度/新近度） |
| `mira/store.py` | 唯一读写数据库的地方 |
| `mira/backup.py` | 每日自动备份数据库，清理旧备份 |
| `mira/desktop.py` | 桌面窗口入口（App 用它打开自己的窗口） |
| `mira/make_app.py` | 生成 `~/Applications/Mira.app` |
| `mira/prompts/` | 给模型的提示词 |
| `web/` | 聊天页和记忆管理页（纯 HTML/JS，不需要构建） |

## 许可证

[MIT](LICENSE)
