# Arboris-Novel 部署指南

本指南合并原根目录的 `DEPLOYMENT_GUIDE.md`（写作流程升级说明）和 `DEPLOYMENT_GUIDE_FULL.md`（部署运维说明）。命令从仓库根目录执行，部署前备份数据库和私有配置。

## 环境与准备

- 本地开发使用 Python 3.11、Node 22.12+；测试与开发命令见 [中文 README](../README.md#面向开发者)。
- 容器部署需要 Docker Engine/Desktop、Compose v2；镜像使用 Python 3.11。
- 推荐至少 2 核、4GB 内存和 20GB 可用磁盘，磁盘容量需随小说与向量数据增长调整。
- 默认部署可使用 SQLite；MySQL 模式需要可访问的 MySQL 8 数据库。

克隆自己的仓库后执行 `cp backend/env.example .env` 并设置私有配置；不要把密钥、密码或数据库文件提交到版本库。Compose 使用的配置字段见 `deploy/docker-compose.yml` 与 [环境变量说明](../README.md#环境变量说明)。本地 FastAPI 配置与 Compose 默认值可能不同，应显式选择数据库提供方。

## 配置

| 配置项 | 用途 |
| --- | --- |
| `SECRET_KEY` | 随机生成的 JWT 签名密钥 |
| `DB_PROVIDER` | 显式选择 `sqlite` 或 `mysql` |
| `SQLITE_DB_PATH` / `SQLITE_STORAGE_SOURCE` | 容器 SQLite 文件位置 / 宿主机持久化位置 |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_DATABASE` | MySQL 连接信息 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | MySQL 应用账号；当前 Compose 即使选择 SQLite 也要求提供 MYSQL_PASSWORD |
| `ADMIN_DEFAULT_USERNAME` / `ADMIN_DEFAULT_PASSWORD` | 首次初始化管理员；必须使用自定义密码 |
| `OPENAI_API_KEY` / `OPENAI_API_BASE_URL` / `OPENAI_MODEL_NAME` | OpenAI 兼容服务连接与模型名称 |
| `APP_PORT` | 宿主机对外 HTTP 端口 |
| `VECTOR_DB_URL` / `EMBEDDING_*` | 可选 RAG 存储及嵌入服务 |
| `SMTP_*` / `EMAIL_FROM` | 开启验证码注册时所需的邮件服务 |
| `ALLOW_USER_REGISTRATION` / `ENABLE_LINUXDO_LOGIN` | 注册与 Linux.do 登录开关 |

章节版本数按以下顺序解析：

1. 数据库系统配置 `writer.chapter_versions`，再兼容 `writer.version_count`。
2. 环境变量 `WRITER_CHAPTER_VERSION_COUNT`、`WRITER_CHAPTER_VERSIONS`、`WRITER_VERSION_COUNT`。
3. `settings.writer_chapter_versions`，默认 2。

旧高级 pipeline 的 preview、critique、reader simulation 等配置已随无用流程移除，不用于当前标准章节生成。

## 构建与启动

SQLite 模式，先在私有配置中设置 `DB_PROVIDER=sqlite`：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml ps
```

Compose 内置 MySQL 模式：配置 `DB_PROVIDER=mysql` 及 MySQL 参数后执行：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml --profile mysql up -d --build
```

使用外部 MySQL 时配置其地址，不启用内置 mysql profile。前端、FastAPI、Nginx 和 Supervisor 使用同一个 app 镜像；构建配置见 `deploy/Dockerfile`，服务编排见 `deploy/supervisord.conf`。

`deploy/scripts/quick_deploy.sh`、`deploy_docker.sh` 和 `server_deploy.sh` 提供旧的一键部署流程。运行前先检查其中的仓库、主机、路径和数据库假设；不要直接沿用历史服务器地址。本文不执行远程部署。

## 数据库兼容与迁移

应用启动的 `app/db/init_db.py` 会创建缺少的表、初始化首个管理员和默认配置/提示词，并检查 `chapter_outlines.metadata`，仅在缺列时补齐。已有提示词内容不会自动覆盖。

其余历史迁移位于 `backend/db/migrations/`；旧脚本也提供 `deploy/scripts/run_migrations.sh` 与 `verify_migration.sh`。先备份、检查数据库提供方及实际表结构，再决定是否执行匹配的迁移。不要把曾经执行过的 ALTER 重复执行，也不要为重试或回滚删除现有表/列。

阶段 0 只删除无用流程代码，不提供删表迁移。保留现有用户数据与数据库对象；代码回滚不等同于数据库回滚。

## 提示词与写作验收

在后台提示词管理中核对下列模板。新增名称会在初始化时导入；需要更新已有内容时由管理员确认后修改。

| 名称 | 文件 | 用途 |
| --- | --- | --- |
| `concept` | `backend/prompts/concept.md` | 灵感对话 |
| `screenwriting` | `backend/prompts/screenwriting.md` | 蓝图生成 |
| `outline_generation` | `backend/prompts/outline_generation.md` | 续写章节大纲 |
| `chapter_plan` | `backend/prompts/chapter_plan.md` | L2 导演脚本 |
| `writing_v2` / `writing` | `backend/prompts/writing_v2.md` / `writing.md` | 正文，优先 v2 |
| `rewrite_guardrails` | `backend/prompts/rewrite_guardrails.md` | 违规内容修复 |
| `editor_review` | `backend/prompts/editor_review.md` | 多版本比较 |
| `extraction` | `backend/prompts/extraction.md` | 章节摘要 |

先执行离线 `make -k check`，然后在授权的测试环境人工验收：

- 登录、创建项目、灵感对话与蓝图保存正常。
- 大纲续写保留已有章节；配置两个版本时生成两个候选。
- 生成日志中可看到导演脚本与信息可见性统计；护栏违规会记录检查结果。
- 候选待确认，人工选择后成为最终内容；非法 JSON、长度截断或连接异常有相应错误。

自动测试只用 mock，不代表已经验证真实模型或生产网络。

## 健康检查与日志

```bash
curl --fail http://127.0.0.1/api/health
docker compose --env-file .env -f deploy/docker-compose.yml logs --tail=100 app
docker compose --env-file .env -f deploy/docker-compose.yml logs -f app
```

按实际 APP_PORT 修改 curl 端口。健康响应包含 `status=healthy`、`app`、`version`。后端本地 uvicorn 的 `/docs` 可用于检查接口。容器内日志位置由 Supervisor/Nginx 配置决定，数据库日志用 MySQL 服务或外部数据库的工具查看。

## 故障排查

- 容器无法启动：先查日志、端口映射、必需配置与数据库目录权限。
- 数据库连接失败：检查提供方、地址、网络与账号权限；SQLite 检查卷和父目录。
- 缺少写作提示词：确认 writing_v2 或 writing 存在；摘要需要 extraction。
- 导演脚本失败：日志会说明原因，现有实现回退默认模式；对照 chapter_plan 和模型返回的 JSON。
- 护栏没有触发：仅检测现有规则中的违规；先核对裁剪后蓝图与禁止角色集合，不以无告警推断所有内容正确。
- 网页无法访问：检查宿主机端口、防火墙、Nginx 和 app 日志。
- AI 超时、非法 JSON 或长度截断：检查模型能力与配置，不在诊断过程中自动批量重试生产请求。

## 更新、备份与回滚

更新前记录当前镜像/提交并备份数据库、向量存储和私有配置。SQLite 应在停止写入时复制一致的文件，MySQL 使用其备份工具。旧 `deploy/scripts/rollback.sh` 需先核对环境后再使用。

回滚时恢复经过验证的代码或镜像，并在需要时恢复对应的完整数据库备份，再启动和执行健康检查。不要硬编码历史提交，也不要通过删 `metadata` 列来配合代码回滚。

生产环境应配置 HTTPS 反代、持久化与备份、资源监控及按实际来源限制的网络访问。清理镜像前确认保留了可回滚版本；不要对持久化数据库卷执行自动清理。
