# Mira

> A personal AI friend with long-term memory that runs on your own computer. Mira remembers what you tell her, notices recurring patterns in your goals and moods, and — like a good friend — gently points out problems at the right moment. Chinese-first UI; uses the DeepSeek API; all data stays in a local SQLite file. MIT licensed.

Mira 是一个运行在你自己电脑上的 AI 朋友。她会记得你说过的话，留意你反复出现的模式，在合适的时候温和地指出你的问题。

## 她能做什么

- **长期记忆**：聊完后，她会自动整理值得记住的东西，分成事实、人物、承诺、模式、事件几类
- **诤友**：聪明、理性、温柔。你情绪不好时，她先陪着你；你状态好的时候，她会有理有据地指出问题（比如"你上周说周五前改完简历的"）
- **每日反思**：每天回顾一次，发现你反复出现的行为和情绪模式，更新她对你的整体了解（核心档案）
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

### 只想先看看界面？

开发模式下用的是假回复，不需要 API key，也不会下载模型：

```bash
MIRA_FAKE=1 DB_PATH=data/dev.db uv run python -m mira
```

## 日常使用

- **备份**：复制 `data/mira.db` 就行（如果有 `mira.db-wal` 文件，请先停止 Mira 再复制）
- **修改人设**：编辑项目根目录下的 `persona.md`，重启后生效
- **纠正记忆**：在记忆管理页（右上角"记忆"）修改或删除。你改过的记忆会被锁定，她不会再自动改动

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
| `mira/retriever.py` | 混合检索（语义 + 关键词 + 重要度/新近度） |
| `mira/store.py` | 唯一读写数据库的地方 |
| `mira/prompts/` | 给模型的提示词 |
| `web/` | 聊天页和记忆管理页（纯 HTML/JS，不需要构建） |

## 许可证

[MIT](LICENSE)
