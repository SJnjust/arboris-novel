# Arboris-Novel | 面向创作者的写作辅助工具

**[English](README-en.md)** | 中文

![GitHub stars](https://img.shields.io/github/stars/t59688/arboris-novel?style=social)
![GitHub forks](https://img.shields.io/github/forks/t59688/arboris-novel?style=social)
![GitHub issues](https://img.shields.io/github/issues/t59688/arboris-novel)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

如果你想使用命令行+编辑器的方式，可搭配使用 [novel-kit](https://github.com/t59688/novel-kit)。

写作时容易卡在「主角叫什么」「故事发生在哪」「下一章写什么」这类问题上。**Arboris** 在需要时帮你理清思路、记录设定、给出可选方向，让想法落成故事。

**在线体验：** [https://arboris.aozhiai.com](https://arboris.aozhiai.com)

<p align="center">
  <table align="center">
    <tr>
      <td align="center"><strong>交流群</strong><br/><img width="220" alt="交流群二维码" src="https://github.com/user-attachments/assets/6d4fe420-f8ae-4fe4-883d-235eb576c83b" /></td>
      <td align="center"><strong>作者公众号</strong><br/><img width="220" alt="作者公众号" src="https://picui.ogmua.cn/s1/2026/02/24/699d109e4ced2.webp" /></td>
    </tr>
  </table>
</p>

---

## 界面预览

<p align="center">
  <img width="1471" alt="主界面" src="https://github.com/user-attachments/assets/a52d0214-bc1b-4792-8a2b-267b09e47379" />
</p>
<p align="center">
  <img width="1375" alt="角色管理" src="https://github.com/user-attachments/assets/0673faad-43df-4479-83ae-cffa870199a3" />
</p>
<p align="center">
  <img width="1392" alt="大纲编辑" src="https://github.com/user-attachments/assets/b7a7af24-1689-4341-aa78-26b0d74bdddd" />
</p>
<p align="center">
  <img width="1255" alt="写作界面" src="https://github.com/user-attachments/assets/c831d746-8c1a-4ce8-aa1c-9b852da15c11" />
</p>

---

## 功能概览

### 设定管理
角色、地点、派系等设定集中记录，随时查阅，避免写到后期前后矛盾（如角色外貌、世界观规则等）。

### 大纲与故事线
零散的场景和灵感可交给 AI 梳理，生成从开头到结局的主线大纲。

### 写作辅助
状态不佳时可让 AI 先出草稿再按自己的风格修改；也可自己写开头，让 AI 续写以获取灵感。

### 多版本对比
支持一次生成多版内容，挑选最符合风格的部分，逐步让模型更贴合你的笔触。

---

## 项目初衷

目标是做一个**能记住你的世界、理解角色、随故事推进的写作伙伴**，而不是单纯的自动生成器。因此做了 Arboris 并选择开源，方便更多创作者使用。

---

## 快速开始

### 方式一：Docker 部署

```bash
# 1. 复制配置文件
cp backend/env.example .env

# 2. 编辑 .env 中的必填项：
#    - SECRET_KEY: 随机字符串，用于 JWT 等
#    - OPENAI_API_KEY: 大模型 API Key
#    - ADMIN_DEFAULT_PASSWORD: 管理员密码（勿用默认值）

# 3. 启动（默认 SQLite，无需单独安装数据库）
docker compose up -d

# 启动后在浏览器访问 http://localhost:<端口>
```

### 方式二：使用 MySQL（Compose 内 MySQL）

```bash
# .env 中设置 DB_PROVIDER=mysql，然后执行：
DB_PROVIDER=mysql docker compose --profile mysql up -d
```

### 方式三：使用自有 MySQL

```bash
# 在 .env 中配置数据库地址、用户名、密码后执行：
DB_PROVIDER=mysql docker compose up -d
```

---

## 环境变量说明

常用配置如下（完整项见 `.env.example`）：

| 配置项 | 必填吗 | 说明 |
|--------|--------|------|
| `SECRET_KEY` | ✅ | JWT 加密密钥，需自行随机生成并妥善保管 |
| `OPENAI_API_KEY` | ✅ | 你的 LLM API Key（OpenAI 或兼容的） |
| `OPENAI_API_BASE_URL` | ❌ | API 地址，默认是 OpenAI 官方的 |
| `OPENAI_MODEL_NAME` | ❌ | 模型名称，默认 `gpt-3.5-turbo` |
| `ADMIN_DEFAULT_PASSWORD` | ❌ | 管理员初始密码，部署后务必修改 |
| `ALLOW_USER_REGISTRATION` | ❌ | 是否开放注册，默认 `false` |
| `SMTP_SERVER` / `SMTP_USERNAME` | 开放注册时必填 | 邮件服务，用于发送验证码 |

> **数据存储：** 默认 SQLite，数据在 Docker 卷中。需映射到本地时，在 `.env` 中设置 `SQLITE_STORAGE_SOURCE=./storage`。

---

## 常见问题

### 基础使用

**Q: 不会用 Docker？**  
A: 安装 Docker Desktop（Windows/Mac）或 Docker Engine（Linux），按上文命令执行即可。

**Q: API Key 会泄露吗？**  
A: 不会。密钥仅存在于服务端 `.env`，不向前端或用户暴露。

**Q: 是否支持其他大模型？**  
A: 支持。只要提供 OpenAI 兼容接口，在 `.env` 中配置 `OPENAI_API_BASE_URL` 即可。

**Q: 修改了代码如何参与？**  
A: 欢迎提交 PR 或 Issue。

### 生成小说时的常见错误

**Q: 提示"未配置默认 LLM API Key"怎么办？**  
A: 检查 `.env` 文件中的 `OPENAI_API_KEY` 是否正确配置。如果是个人用户，也可以在个人设置中配置自定义 API Key。

**Q: 生成时提示"今日请求次数已达上限"？**  
A: 系统管理员可能设置了每日请求限制。解决方案：
- 等到明天再试
- 在个人设置中配置自己的 API Key（不受系统配额限制）
- 管理员调整配额限制（修改 `daily_request_limit` 配置）

**Q: 提示"AI 服务响应超时"或"无法连接到 AI 服务"？**  
A: 网络或 API 服务问题导致。可以：
- 检查网络连接是否正常
- 确认 `OPENAI_API_BASE_URL` 配置是否正确
- 如果使用自建服务，检查服务是否正常运行
- 稍后重试

**Q: 提示"AI 响应因长度限制被截断"？**  
A: 生成的内容超过了模型的输出限制。建议：
- 使用支持更长输出的模型

**Q: 提示"AI 未返回有效内容"或"AI 服务内部错误"？**  
A: AI 服务端出现问题。通常是暂时性的，可以：
- 大多是LLM服务的问题，尤其是逆向的API。
- 检查 API Key 是否有效且有足够余额
- 查看后端日志获取详细错误信息

**Q: 提示"蓝图中未找到对应章节纲要"？**  
A: 在生成章节内容前，需要先在蓝图（大纲）中创建对应章节的纲要。请先完善章节大纲再进行生成。

**Q: 提示"未配置摘要提示词"？**  
A: 系统缺少必要的 Prompt 配置。管理员需要在后台配置名为 `extraction` 的提示词模板，用于生成章节摘要。

**Q: 提示"AI 返回的内容格式不正确"或 JSON 解析错误？**（较常见）  
A: AI 返回内容无法解析为有效 JSON。可能原因与处理方式：
- **原因 1：模型能力不足** - 某些模型难以稳定输出结构化 JSON
  - 解决：切换到能力更强的模型
  - 或使用支持 structured output 的模型
- **原因 2：内容过长** - 某些逆向API可能无法支持长输出。

- **临时处理：** 重试几次，或更换 AI 模型

**Q: 生成的内容质量不理想怎么办？**  
A: 可以尝试：
- 完善角色、地点、派系等设定信息
- 优化章节纲要，提供更详细的指引
- 使用多版本生成功能，让 AI 生成多个版本后挑选最佳的
- 调整使用的模型，需要长上下文的

---

## 技术栈

- **后端：** Python + FastAPI
- **数据库：** SQLite（默认）或 MySQL + libsql
- **前端：** Vue + TailwindCSS
- **部署：** Docker + Docker Compose
- **AI：** OpenAI API 或兼容接口

---

## 面向开发者

### 环境准备

- Python 3.11（与 `deploy/Dockerfile` 一致），后端虚拟环境使用 `backend/.venv`
- Node.js 22.12+ 与 npm（本轮使用 Node 22.23.3；请先激活本机的 Node 22）
- pip / venv；macOS 缺少 Python 3.11 时可执行 `brew install python@3.11`
- 可选：Docker 与 Docker Compose（用于一键部署与发布）

### 后端本地开发

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp env.example .env
# 编辑本地 .env，填写自己的值，不提交；本地开发显式选择 SQLite：
# SECRET_KEY=<本地随机值> DB_PROVIDER=sqlite SQLITE_DB_PATH=/绝对路径/local.db
uvicorn app.main:app --reload
```

默认会监听 `http://127.0.0.1:8000`，你可以通过 `--host`、`--port` 调整，或加上 `--reload` 保持热重载。

### 前端本地开发

```bash
cd frontend
npm ci
npm run dev
```

开发服务器默认运行在 `http://127.0.0.1:5173`，可通过 `--host` 参数暴露给局域网设备。

### 测试与静态检查

从根目录执行：

```bash
make test                # backend/.venv 的 pytest，仅运行 backend/tests
make check               # 后端/前端测试 + type-check + build-only；任一步失败返回非零
make -k check            # 即使 type-check 失败，也继续执行独立的构建并报告结果
```

`make check` 还会运行前端 `npm test`：使用已有 Vue/TypeScript 和 Node VM 验证请求超时、422 错误、任务恢复/取消与轮询，不增加测试依赖。

对应的独立命令：

```bash
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm run type-check
npm run build-only
```

后端测试使用每个用例独立的临时 SQLite、真实鉴权/路由/服务和数据库提交；LLM 客户端使用脚本化流式 mock，模型配置与外部向量入库打桩，禁止发出网络连接。测试不读取 `.env`，不会访问真实模型、邮件、Redis 或用户数据库。覆盖登录、项目归属、灵感对话、蓝图生成/替换、续写大纲、多版本生成、选择与失败状态、非法 JSON 和长度截断错误。

前端类型检查与 Vite 构建应分别执行：构建成功不代表类型检查通过。类型问题的阶段 0 基线分类与计数见本次开发报告；后续修改应继续使用这些命令检查，不通过修改业务行为来掩盖诊断。

### AI 后台任务（R1）

章节生成改为后台任务：`POST /api/writer/novels/{id}/chapters/generate` 返回 **202 `{ "job_id": "..." }`**，不再同步返回小说对象。提交可带 `Idempotency-Key`（1–128 字符，区分大小写）；同一用户、同一键返回原任务（包括已结束任务），不同键对同一项目/类型/章节的活动任务返回 409 并附 job_id。默认每用户最多两个 queued/running 任务，超过返回 429 与 Retry-After。

- `GET /api/jobs/{id}` 查看状态、阶段、进度、结果/错误和心跳。
- `GET /api/jobs?project_id={id}&active=1` 恢复进行中的任务；只返回当前用户的任务，列表上限 100。
- `POST /api/jobs/{id}/cancel` 取消；运行分支一起取消并清理章节状态。已结束的任务保持原状态。

前端按 1.5s 起、每次乘 1.5、最多 8s 轮询；隐藏标签页暂停，重新可见或刷新后从服务端/本地任务 ID 恢复。网络失败最多重试三次，之后可手动重试；离开页面只终止本页请求和轮询，不隐式取消后台生成，需要停止时使用取消按钮。

任务依次回补近期摘要、规划本章、检索上下文、生成候选、比较版本并保存。摘要回补并发最多 3，版本生成并发最多 2，各分支使用独立会话；长模型等待不占用章节任务的数据库事务。只要一个版本成功就保存，失败版本信息保存在任务结果与成功版本 metadata 中；全失败/取消时将进行中的章节恢复为 failed。

默认密钥的每日限额在任务开始原子预留一次，结束后把其余已完成的模型调用统一归入任务起始日；如果失败或取消前没有完成任何调用，会退还预留。个人自定义密钥延用原来的每日限额豁免。已接受的并行任务不会因中途跨过日限额而丢弃候选，后续提交会被拒绝；此计数不是 token 账单。

配置通过环境变量提供（名称与 Settings 字段对应，未设置时使用以下默认值）：

| 变量 | 默认 | 说明 |
| --- | ---: | --- |
| `AI_JOB_USER_CONCURRENCY` | 2 | 每用户活动任务数，1–16 |
| `AI_JOB_RETENTION_DAYS` | 7 | 终态任务及幂等记录保留天数；提交时最多每小时清理一次 |
| `CHAPTER_SUMMARY_BACKFILL_LIMIT` | 5 | 只回补最近 N 个已确认章节的缺失摘要，0–50 |
| `LLM_DEADLINE_SECONDS` | 600 | 单次逻辑生成（含最多两次纯文本续写）的总上限，仍取调用方 timeout 的较小值 |
| `HTTP_REQUEST_DEADLINE_SECONDS` | 90 | 仅显式标记的短接口（任务提交/查询/取消、卷规划编辑）的时限，最大 95s，超时返回中文 504 |

SDK 传输超时为 connect=10/read=90/write=30/pool=10 秒，重试 1 次；客户端按凭据/地址缓存，最多 64 个空闲/在用缓存项（全在用时临时实例在调用结束关闭）。纯文本因 length 截断时最多续写两次；JSON 不续写，返回明确错误。JSON 统一由 parse_llm_json 做无损修复（包裹、尾逗号等）；所有业务调用显式禁止补齐截断字符串/容器或丢弃尾部元素。需要补齐或丢元素的解析必须使用 allow_lossy=True，目前没有业务调用启用。残缺 JSON 章节版本不会作为普通正文保存。

部署必须保持 **单个 uvicorn worker**（现有 supervisord.conf 已设置 workers=1）：任务引用、准入锁和取消信号保存在单进程中。启动先初始化数据库，再将上次 queued/running 任务标记为中断失败，修复 generating/evaluating/selecting 章节；退出时取消任务并关闭模型连接池。SQLite 自动启用 WAL 和 30 秒 busy timeout。

章节生成、蓝图和卷大纲已使用后台任务，灵感对话使用 R2 SSE。短 HTTP 时限通过路由装饰器启用，响应流发送不受此时限取消。保留的同步概念对话、大纲续写、评审、优化、导入、选定版本嵌入等保持各自原有后端时长，前端这些调用用 timeoutMs: null 关闭新增的请求计时器；普通请求默认仍为 30 秒，调用者仍可通过 AbortSignal 取消。Cloudflare 对这些旧同步调用的约 100 秒等待限制仍在，需要后续迁移解决；本地/内网可继续等待原来的生成过程。

### 灵感流式对话与提示词版本（R2）

灵感模式使用 `POST /api/novels/{id}/concept/converse/stream`，返回 `text/event-stream`，禁用代理缓冲和响应缓存。请求仍带 `user_input`、`conversation_state`，新增 `client_msg_id`；前端为每轮生成 ID，失败时保留聊天与项目，用同一 ID“重试本轮”。离开页面会中止本页请求及对应的上游流，返回页面可从已保存的用户消息恢复尚未完成的一轮。

- `delta`：`{"text":"回复增量"}`，只发送根对象 ai_message 的文本；不发送 ui_control 或状态 JSON。
- `ping`：`{}`，默认每 15 秒一次，持续有 delta 时也照常心跳。`CONCEPT_PING_SECONDS` 可配置，范围大于 0 且不超过 60 秒。
- `final`：完整 ConverseResponse，与原同步接口结构相同；前端此时更新输入选项与对话状态。
- `error`：`{"detail":"错误说明","retryable":true}`；已收到的回复保留在原消息上。

旧同步端点保留，并和流式端点共用服务。带消息 ID 时，用户消息先保存并按项目内的 ID 去重，assistant 仅在完整 JSON 通过严格解析/校验后保存。同 ID 已完成的回复直接重放，不重复调用模型；同 ID 换输入返回冲突。未带 ID 的旧调用仍保持失败时不保存新用户消息的兼容行为。最终数据库提交后才发送 final，网络丢失 final 时可重放。每个项目在单 worker 内串行处理，锁在没有使用者时释放。

模型上下文仅保留最近 12 条消息（包括当前输入），assistant 只保留 ai_message；完整 conversation_state 存在 assistant metadata，并以 `[已收集信息]` 加到系统提示词。旧的完整 JSON 历史也会投影成上述格式。项目 conversation_history 响应增加可选 metadata，便于恢复被中断轮次的 ID。LLMService.stream_text 与同步收集共享 R1 的总 deadline、长度续写与客户端设置；SSE 路由不加短请求时限。零次完成调用的中断会退还默认密钥预留额度。

蓝图确认页显示 R3 后台任务的真实阶段和进度，支持取消、刷新恢复和失败续跑；不再用 180 秒定时器制造超时或模拟进度。

所有发行提示词以 `<!-- version: N -->` 开头，加载时剥离版本头。启动迁移会检查列后补齐 prompts.version / is_customized（SQLite/MySQL），缺失模板自动插入，文件版本更高且未定制时更新。首次升级先比对旧库内容，和发行文件不同的标为定制，保护管理员修改；concept v1 的发行正文保留在 prompts/baselines/，可识别并升级到使用 `[已收集信息]` 的 v2。后台新建/编辑模板都标记为定制，不再自动覆盖；更新文件正文时需要递增版本号。

### 自动分卷与分步蓝图（R3）

`POST /api/novels/{id}/blueprint/generate` 现在返回 **202 `{job_id}`**。按核心设定 → 分卷规划 → 第 1 卷大纲（每批最多 15 章）执行，前端复用 useJob 轮询，不让整个生成过程占用一个长 HTTP 请求。请求可带 Idempotency-Key 和 `{ "resume": true }`（默认）；失败/取消后以新任务续跑已保存的检查点。显式 `{ "resume": false }` 重新规划，但仍保留已有正文对应的大纲。蓝图确认页能显示“第 X 卷 · x/y 批”、取消和刷新恢复；进入灵感模式创建项目后会将项目 ID 留在 URL。

| 接口 | 行为 |
| --- | --- |
| GET `/api/novels/{id}/volumes` | 返回卷规划；老目录提供虚拟第 1 卷 |
| POST `/api/novels/{id}/volumes/{n}/outline` | 202，生成/续跑本卷缺失大纲 |
| POST `/api/novels/{id}/volumes/outline-all` | 202，按顺序补全全部剩余卷 |
| POST `/api/novels/{id}/volumes/smart-split` | 202，只根据已有大纲标题/摘要分卷，保留大纲内容 |
| PATCH `/api/novels/{id}/volumes/{n}` | 编辑卷名、梗概、起止章号，返回最新项目 |

每卷规划 20–120 章，总章数不足 20 时合并为单卷；新的自动规划总章数范围为 1–10000。既有更长目录仍可读取，不按新生成上限截断旧数据。无效缺口/重叠可规范化为连续区间，卷数太少而无法覆盖总章数时明确失败，不虚构缺失的故事计划。每批大纲必须覆盖请求章号，越界和重复条目不会混入数据库，缺章或截断整批拒绝。批次与 outline_cursor 同一事务提交，重试重新核对数据库中的连续前缀，不重复或跳过已保存批次。

大纲上下文使用约 2000 字的蓝图摘要、本卷计划、上一卷摘要和最近 10 条大纲，不发送全部逐章目录。智能分卷对长目录使用最多 100 组标题/摘要采样，不发送正文。screenwriting.md 升至 v2（只输出核心设定与目标章数范围）；新增 volume_planning.md 和 volume_outline.md，旧 v1 发行正文保留为升级比对基线。管理员定制仍受提示词版本策略保护，核心调用额外声明 R3 输出契约。

保存/重生成蓝图会保留**任何非空正文版本**对应的大纲（含未确认的候选稿），包括标题、摘要和 metadata。卷范围不能重叠，改范围须满足卷长度限制，也不能把已有正文移出原卷；已有正文对应的大纲不被自动覆盖。后台规划与手工大纲/卷编辑、章节生成互斥，冲突返回 409 和活动 job_id，防止旧页面的保存覆盖正在生成的批次。

旧项目没有卷记录时，序列化为 1–最大已有章号的虚拟单卷；空项目不虚构章号。原平铺 chapter_outline 字段保留，新增 volumes、total_chapters、volume_number，目录按卷折叠显示“已写 / 已有大纲 / 计划章数”。手工编辑产生的未分配章节仍在“未分卷章节”中可见。修改梗概不会自动重写已经存在的大纲，卷任务补齐缺失部分；重新生成蓝图可重做未写正文的大纲。

启动迁移通过 inspector 检查后补齐旧表字段，创建 novel_volumes，并将中断卷的 outlining 改回 planned，保留 cursor。只支持已有的 SQLite/MySQL；仍须部署为单 uvicorn worker。兼容的同步 `/chapters/outline` 接口保留，限制单次 1–15 章并使用有界上下文；正常的按卷生成走后台任务。

### 打包与构建

- 前端：`npm run build`，构建产物位于 `frontend/dist/`
- 后端：确认依赖锁定后，可使用 `pip install -r requirements.txt` 安装到目标环境，或基于 `deploy/Dockerfile` 构建镜像
- 静态文件托管：生产环境下可用 Nginx 等服务托管 `dist` 目录，并由后端提供 API

### 发布与部署

推荐在根目录下使用 Compose 文件完成一体化部署：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

完整部署、数据库兼容、提示词和回滚说明已合并到 [部署指南](docs/DEPLOYMENT_GUIDE.md)。历史分析与集成方案保留在 [文档归档](docs/archive/README.md)。

如需推送镜像，可在 `deploy` 目录执行 `docker build -t <registry>/arboris:<tag> .`，测试后再 `docker push` 发布。

---

## 参与贡献

- Star 项目
- 在 Issues 中反馈 Bug 或建议
- 提交 PR 贡献代码
- 通过文首二维码加入交流群

---

## 反馈与致谢

使用 Arboris 写出作品后，欢迎与我们分享。祝写作顺利。

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

[![Star History Chart](https://api.star-history.com/svg?repos=t59688/arboris-novel&type=Date)](https://star-history.com/#t59688/arboris-novel&Date)
