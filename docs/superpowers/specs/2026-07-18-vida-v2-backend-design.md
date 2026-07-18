# VIDA 2.0 后端重构设计

- 日期：2026-07-18
- 状态：设计对话已确认，待用户审阅书面稿
- 输入契约：[`docs/api/v2-api-contract.md`](../../api/v2-api-contract.md)
- 兼容目标：保留 VIDA 1.x 的静态前端与既有 `/api/*` 接口

## 1. 背景与目标

当前 VIDA 后端是一个 FastAPI 单体。`backend/main.py` 同时承担路由、任务状态、文件持久化、
异步调度和处理管线；任务记录保存在 `temp/tasks.json`，生成内容保存在 `temp/` 下的 Markdown
文件中。该结构可以支持 1.x 的单任务交互，但不能稳定承载 VIDA 2.0 所需的项目模型、持久任务
队列、结构化转录、章节、Provider Profile 和服务重启恢复。

本次重构的目标是：

1. 在不破坏 1.x 的前提下提供完整的 `/api/v2` 后端。
2. 使用 SQLite 保存结构化元数据，文件系统保存媒体及生成产物。
3. 支持客户端连续提交上传或 URL 项目，并通过持久 FIFO 队列限制后台最大并发数。
4. 服务重启后自动恢复排队和处理中断的任务。
5. 提供 Provider Profile 管理 API，并加密保存 API Key。
6. 提供转录、摘要、自动章节、导出、取消、重试及独立重新生成能力。

本次不迁移 1.x 历史数据。`temp/`、`temp/tasks.json` 和历史 Markdown 继续由 1.x 使用，v2 只
管理实施后新创建的项目。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 后端形态 | 模块化单体 FastAPI |
| 元数据存储 | SQLite |
| 大文件存储 | 独立 v2 文件目录 |
| 历史数据 | 不导入、不迁移 |
| 创建来源 | 文件上传和 URL 均支持 |
| 队列 | SQLite 为事实来源的持久 FIFO 队列 |
| 并发 | 启动配置，默认 2 |
| 重启恢复 | queued 与中断的 processing 均重新排队，从管线开头执行 |
| 状态 | queued、processing、completed、failed、canceled |
| 队列可见性 | progress、message、queuePosition |
| 调度优先级 | 全局 FIFO；重试进入队尾 |
| Provider | 通过完整 CRUD API 录入，不从 Provider 配置文件或环境变量初始化 |
| Profile 版本 | 任务入队时固定不可变 revision |
| Provider 密钥 | 使用服务端主密钥加密后保存 |
| 章节 | 处理完成时自动生成，也支持手动新增 |
| AI 降级 | 转录成功即可完成；优化、摘要、章节失败记录 warning |
| 文件保留 | 成功与失败项目均长期保留，未来由显式删除接口清理 |
| 第一版部署 | 单 FastAPI/Uvicorn 进程，进程内多个 Worker |

## 3. 总体架构

采用“模块化单体 + SQLite 持久队列”方案。FastAPI 仍作为一个服务部署；SQLite 保存项目、
任务、Provider Profile revision、章节及产物索引；启动时创建固定数量的异步 Worker。Worker
通过事务领取最早的 queued Job，并持续写入进度。

第一版只支持单服务实例和单 Uvicorn 进程。应用并发由内部 Worker 数控制，不通过增加 Uvicorn
worker 数扩展。未来需要横向扩容时，可替换 Repository 与任务执行层，HTTP 契约和业务 Service
边界保持不变。

### 3.1 模块结构

```text
backend/
├── main.py                       # 应用入口；保留 1.x，挂载 v2 router
├── v2/
│   ├── router.py                 # 汇总 /api/v2 路由
│   ├── config.py                 # 启动配置及校验
│   ├── schemas.py                # Pydantic 请求/响应模型、camelCase
│   ├── errors.py                 # 统一错误码与异常处理
│   ├── database.py               # SQLite 连接、WAL、事务、迁移
│   ├── repositories/
│   │   ├── episodes.py           # 项目、转录、摘要和处理状态
│   │   ├── jobs.py               # 队列领取、进度、取消、恢复
│   │   ├── chapters.py           # 章节持久化
│   │   └── provider_profiles.py  # Profile 与不可变 revision
│   ├── services/
│   │   ├── episode_service.py    # 创建、查询、重试、取消
│   │   ├── dashboard_service.py  # 首屏聚合读取
│   │   ├── export_service.py     # TXT、SRT、Markdown 导出
│   │   └── media_service.py      # 媒体路径、URL、文件生命周期
│   └── jobs/
│       ├── scheduler.py           # FIFO 调度与 Worker 生命周期
│       ├── worker.py              # 领取、执行和状态转换
│       └── pipeline.py            # URL/上传统一处理管线
└── ...

tests/v2/
├── test_api_contract.py
├── test_job_scheduler.py
├── test_job_recovery.py
├── test_episode_repository.py
├── test_provider_profiles.py
└── test_exports.py
```

### 3.2 边界规则

- API 层只负责协议解析、状态码与响应序列化，不直接访问 SQLite 或磁盘。
- Service 层实现业务规则，包括重试、取消、Dashboard 聚合和文件生命周期。
- Repository 层是唯一允许执行 SQL 的位置。
- Scheduler/Worker 只通过 Repository 领取任务，不依赖 FastAPI 请求对象。
- Pipeline 封装现有 `VideoProcessor`、`Transcriber`、`Summarizer` 和 `Translator`。
- 文件系统只保存大文件和生成产物，SQLite 只保存结构化元数据和相对路径。
- 第一阶段不搬迁或重写现有 1.x 路由；先用回归测试锁定其行为。

SQLite 使用 Python 标准库 `sqlite3` 和显式 SQL，不引入 ORM。数据库启用版本化 SQL migration，
为未来替换成 PostgreSQL Repository 保留清晰接口。

## 4. 启动配置

`start.py` 延续“命令行参数优先、环境变量兜底”的规则。

| 参数 | 环境变量 | 默认值 | 规则 |
|---|---|---:|---|
| `--max-concurrent-jobs` | `V2_MAX_CONCURRENT_JOBS` | 2 | 必须为正整数 |
| `--profile-master-key` | `VIDA_PROFILE_MASTER_KEY` | 无 | Base64 解码后必须为 32 字节 |

生产环境应使用 `VIDA_PROFILE_MASTER_KEY`，避免主密钥出现在进程参数列表中。主密钥缺失、格式
错误或无法解密已有 Profile 时，服务拒绝启动，不允许降级为明文存储。

v2 上传使用独立配置 `V2_UPLOAD_MAX_GB`，默认 5GB。它不改变 1.x 的 `UPLOAD_MAX_MB=200`。
上传采用流式落盘；超过限制或客户端中断时立即清理未完成文件。

## 5. SQLite 数据模型

### 5.1 Episode 与 Job

`Episode` 是用户可见项目，`Job` 表示一次处理尝试。一个 Episode 可因重试拥有多个 Job，
但任何时刻最多只有一个 queued 或 processing Job。

```text
episodes
- id                         UUID，主键
- title
- source_type                upload | url
- source_path                上传文件相对路径，可空
- source_url                 URL 来源，可空
- media_path
- poster_path
- duration_sec
- resolution
- status                     queued | processing | completed | failed | canceled
- language
- progress                   0–100
- message
- error_code / error_message
- current_job_id
- provider_profile_id
- created_at / updated_at / completed_at

jobs
- id                         UUID，主键
- episode_id
- type                       process_episode | regenerate_summary | regenerate_chapters
- attempt                    从 1 开始
- status
- provider_profile_revision_id
- submitted_at
- started_at / finished_at
- cancel_requested_at
- heartbeat_at
- progress / message
- error_code / error_message
```

Episode 与当前主处理 Job 的状态、进度和错误必须在同一个事务中更新。独立摘要或章节重新生成
Job 不改变已完成 Episode 的状态。

### 5.2 结构化内容

```text
transcript_segments
- id
- episode_id
- ordinal
- start_sec / end_sec
- speaker
- text

summaries
- episode_id                 一对一
- content
- read_time_min
- key_points
- confidence
- generated_by

chapters
- id
- episode_id
- start_sec
- title
- duration_sec
- thumbnail_path
- bookmarked
- source                     generated | manual
- created_at
```

重新生成章节只替换 `source = generated` 的章节，不删除用户手动添加的章节。重新生成摘要先生成
新值，再在事务中替换旧摘要；失败时旧摘要继续可用。

### 5.3 Provider Profile

```text
provider_profiles
- id
- name
- active_revision_id
- deleted_at
- created_at / updated_at

provider_profile_revisions
- id
- profile_id
- version
- base_url
- model_id
- temperature
- encrypted_api_key
- encryption_nonce
- encryption_format_version
- created_at

schema_migrations
- version
- applied_at
```

任务入队时记录不可变的 `provider_profile_revision_id`。修改 Profile 会创建新 revision 并切换
`active_revision_id`，不会改变已经排队或正在执行的任务。删除 Profile 采用逻辑删除：新任务不能
引用，历史 revision、排队任务和运行任务不受影响。

## 6. 状态机与调度

公开状态为：

```text
创建：             queued
Worker 领取：      queued → processing
正常完成：         processing → completed
核心处理失败：     processing → failed
取消排队任务：     queued → canceled
请求取消运行任务： processing → cancel_requested → canceled
服务重启恢复：     processing → queued
手动重试：         failed/canceled → 新 Job queued
```

`cancel_requested` 是 Job 控制标记，不是公开 Episode 状态。取消尚未完成时 Episode 仍返回
`processing`，但 `message` 显示“正在取消”。

调度规则：

- Worker 使用 `BEGIN IMMEDIATE` 领取 `submitted_at` 最早的 queued Job。
- `submitted_at` 相同则以 Job ID 排序，保证确定性。
- 重试创建新 attempt，并以新的 `submitted_at` 进入队尾。
- `queuePosition` 实时计算，不持久化，避免每次出队批量更新记录。
- 同一 Episode 通过数据库约束最多存在一个活动 Job。
- 上传源文件完整落盘后，才允许创建 Episode 和 queued Job。
- Worker 定期更新 `heartbeat_at`；单个 Job 异常不得终止 Worker 循环。

服务启动时先将遗留 processing Job 恢复为 queued，再启动 Worker。恢复任务从处理管线开头重新
执行，不做阶段级断点续传。

## 7. 任务处理管线

```text
queued
  → 准备任务与 Profile revision
  → 获取或校验来源
  → 提取媒体元数据与封面
  → 转录为结构化分段
  → 文本优化（可降级）
  → 生成摘要（可降级）
  → 自动生成章节（可降级）
  → 原子提交结果
  → completed
```

建议进度区间：

| 阶段 | 进度 |
|---|---:|
| 排队 | 0 |
| Worker 领取与准备 | 1–5 |
| 来源获取、媒体探测 | 5–20 |
| 转录 | 20–60 |
| 文本优化 | 60–72 |
| 摘要生成 | 72–85 |
| 自动章节 | 85–95 |
| 持久化与收尾 | 95–100 |

处理规则：

- 上传项目使用已经完整落盘的源文件。
- URL 项目把可播放媒体下载到 v2 存储，不依赖临时外链。
- `ffprobe` 提取时长、分辨率和媒体类型，FFmpeg 生成封面。
- Whisper 或平台字幕转换为统一的 `TranscriptSegment[]`。
- 来源没有逐段时间戳时，使用 API 契约允许的单段兜底。
- 文本优化失败时使用原始转录。
- 摘要或自动章节失败时 Episode 仍可 completed，并返回结构化 warnings。
- 来源获取、媒体校验、转录或数据库提交失败时 Episode 进入 failed。
- 摘要和章节可以独立重新生成，不必重新执行媒体及转录阶段。

### 7.1 取消

- queued Job 立即取消并释放其队列位置。
- processing Job 设置取消标记，Pipeline 在每个阶段前后检查。
- FFmpeg、下载和网络请求应主动终止。
- Faster-Whisper 的底层计算不能保证立即中断；取消可能要等当前调用返回。
- 取消只清理当前 attempt 的未完成派生产物，保留源媒体和已提交产物。
- Job 变为 canceled 后释放槽位，并调度下一项。

### 7.2 文件布局与原子提交

```text
data/v2/
├── vida-v2.sqlite3
└── episodes/{episodeId}/
    ├── source/
    ├── poster/
    ├── attempts/{jobId}/
    └── artifacts/
```

当前 Job 先写入 `attempts/{jobId}`。完整写入并关闭后，文件通过原子移动进入带版本号的正式
路径，随后数据库事务切换产物指针。若在文件移动和数据库提交之间崩溃，启动恢复扫描会清理
没有数据库引用的孤立文件。数据库不得先指向尚未提交的文件。

上传先写入 `.part`，校验大小和类型后原子改名，再创建 Episode。成功和失败项目的源媒体与
生成结果长期保留，不做自动过期；未来由显式删除接口清理。

## 8. Provider Profile API 与安全

### 8.1 接口

```text
POST   /api/v2/provider-profiles
GET    /api/v2/provider-profiles
GET    /api/v2/provider-profiles/{id}
PATCH  /api/v2/provider-profiles/{id}
DELETE /api/v2/provider-profiles/{id}
POST   /api/v2/provider-profiles/{id}/test
```

创建请求：

```json
{
  "name": "Primary OpenAI",
  "baseUrl": "https://api.openai.com/v1",
  "apiKey": "secret",
  "modelId": "gpt-4.1-mini",
  "temperature": 0.1
}
```

响应不包含密钥：

```json
{
  "id": "uuid",
  "name": "Primary OpenAI",
  "baseUrl": "https://api.openai.com/v1",
  "modelId": "gpt-4.1-mini",
  "temperature": 0.1,
  "hasApiKey": true,
  "apiKeyMasked": "••••••••abcd",
  "revision": 1,
  "active": true,
  "createdAt": "2026-07-18T14:00:00Z",
  "updatedAt": "2026-07-18T14:00:00Z"
}
```

PATCH 省略 `apiKey` 时沿用上一 revision 的凭据；提交非空密钥时加密新凭据。空字符串是验证
错误，不用于清空。任意有效修改均生成新的不可变 revision。

连接测试返回清洗后的结果，不回传上游原始响应、请求头或密钥：

```json
{
  "ok": true,
  "latencyMs": 420,
  "modelAvailable": true,
  "message": "Connection successful"
}
```

### 8.2 凭据加密

增加 `cryptography` 依赖并使用 AES-256-GCM：

- 每次加密使用独立随机 nonce。
- SQLite 只保存 ciphertext、nonce 和加密格式版本。
- API Key 仅在 Worker 执行任务或连接测试时短暂解密。
- 解密结果不得进入 Job、日志、异常或 API 响应。
- 日志过滤 Authorization、API Key 和 URL 查询参数中的密钥。
- Profile 查询只返回掩码和 `hasApiKey`。
- 数据库备份必须同时安全备份主密钥；丢失主密钥后凭据不可恢复。

## 9. v2 API 契约

### 9.1 Episode 模型扩展

```ts
type EpisodeStatus = 'queued' | 'processing' | 'completed' | 'failed' | 'canceled'

interface ProcessingWarning {
  stage: 'optimization' | 'summary' | 'chapters'
  code: string
  message: string
}

interface Episode {
  id: string
  title: string
  sourceType: 'upload' | 'url'
  mediaUrl: string | null
  posterUrl: string | null
  durationSec: number
  resolution?: string
  status: EpisodeStatus
  language: string
  createdAt: string
  progress: number
  message: string
  queuePosition: number | null
  providerProfileId: string
  warnings: ProcessingWarning[]
}
```

排队或处理期间，尚未生成的媒体字段可以为 `null`。`Project.status` 使用同一状态集合。

### 9.2 创建项目

同一路径按 `Content-Type` 区分来源。

上传：

```http
POST /api/v2/episodes
Content-Type: multipart/form-data

file=<binary>
providerProfileId=<uuid>
summaryLanguage=zh
title=<optional>
```

URL：

```http
POST /api/v2/episodes
Content-Type: application/json

{
  "sourceUrl": "https://example.com/media",
  "providerProfileId": "uuid",
  "summaryLanguage": "zh",
  "title": "optional"
}
```

两者均返回 `202 Accepted`，并设置 `Location: /api/v2/episodes/{episodeId}`。

### 9.3 查询、内容与控制

```text
GET  /api/v2/dashboard
GET  /api/v2/episodes?limit=12&offset=0
POST /api/v2/episodes
GET  /api/v2/episodes/{id}
POST /api/v2/episodes/{id}/cancel
POST /api/v2/episodes/{id}/retry
GET  /api/v2/episodes/{id}/transcript
GET  /api/v2/episodes/{id}/summary
POST /api/v2/episodes/{id}/summary/regenerate
GET  /api/v2/episodes/{id}/chapters
POST /api/v2/episodes/{id}/chapters
POST /api/v2/episodes/{id}/chapters/regenerate
GET  /api/v2/episodes/{id}/export?format=txt|srt|md
GET  /api/v2/episodes/{id}/media
GET  /api/v2/episodes/{id}/poster
GET  /api/v2/jobs/{jobId}
POST /api/v2/jobs/{jobId}/cancel
```

完整重试只允许 failed 或 canceled Episode。重复取消 canceled 任务是幂等操作。独立重新生成
任务也进入同一 FIFO 队列并占用并发槽位，但不把 completed Episode 改回 processing。

媒体接口必须支持 HTTP Range 请求，供播放器进行跳转和续播。`mediaUrl` 与 `posterUrl` 返回受控
接口的相对 URL，不暴露磁盘路径。

### 9.4 统一错误

所有 v2 错误，包括 FastAPI 参数校验错误，均转换为：

```json
{
  "error": {
    "code": "provider_profile_not_found",
    "message": "Provider profile does not exist",
    "details": {},
    "requestId": "uuid"
  }
}
```

主要错误码：

| HTTP | code |
|---:|---|
| 400 | `invalid_source`、`invalid_format`、`empty_file` |
| 404 | `episode_not_found`、`summary_not_found`、`provider_profile_not_found` |
| 409 | `invalid_episode_state`、`job_already_active` |
| 413 | `file_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `validation_error`、`provider_profile_inactive` |
| 502 | `provider_connection_failed` |
| 503 | `queue_unavailable` |

未知异常在服务端记录 traceback，客户端只收到通用消息和 requestId。上游 Provider 错误在写入
日志和数据库前必须过滤敏感信息。

## 10. SQLite 事务与错误隔离

初始化参数：

```text
journal_mode = WAL
foreign_keys = ON
busy_timeout = 5000ms
synchronous = NORMAL
```

必须使用显式事务的操作：

- 创建 Episode、首个 Job 和 Profile revision 引用。
- Worker 领取 FIFO Job。
- Job 与 Episode 状态、进度和错误同步更新。
- 重试创建新 Job、切换 current_job_id 和清除旧错误。
- 新增章节及替换自动章节。
- 创建 Profile revision 并切换 active_revision_id。
- Profile 逻辑删除。

数据库暂时锁定时进行有限次数短退避重试。持续失败时停止领取新任务并暴露
`queue_unavailable`，但不能结束整个 Web 进程。Repository 使用绑定参数；排序字段、状态值和分页
字段必须通过白名单。

每个 Worker 拥有独立的请求级 AI 客户端和处理上下文，避免不同 Profile 或任务串用模型配置。

## 11. 兼容性策略

- 保留 `/`、`/static/*` 及现有 `/api/*` 行为。
- v2 只使用 `/api/v2/*`。
- v2 不读取、迁移或修改 `temp/` 和 `temp/tasks.json`。
- 修改前补充 1.x 路由特征测试，锁定响应状态、字段和下载行为。
- 每个实施阶段运行现有 Python 与 JavaScript 测试。
- Docker Compose 增加 v2 并发、上传上限和 Profile 主密钥配置说明。
- 第一版生产部署必须保持单 Uvicorn 进程。

## 12. 测试策略

### 12.1 单元测试

- Pydantic camelCase 序列化与状态枚举。
- SQLite migration、Repository CRUD 和事务回滚。
- API Key 加密、掩码、revision 固定及 Profile 逻辑删除。
- FIFO 排序、queuePosition 和非法状态转换。
- TXT、SRT、Markdown 导出格式。
- 路径解析、文件名清洗与目录逃逸防护。
- 摘要、优化和章节阶段的降级 warning。

### 12.2 并发与恢复测试

使用可控 Fake Pipeline 验证：

- 连续提交 5 个任务时，任意时刻最多 2 个 processing。
- 前两个完成后，其余任务严格按 FIFO 启动。
- 两个 Worker 不会领取同一个 Job。
- 取消 queued 任务后 queuePosition 正确更新。
- 取消 processing 任务后释放槽位。
- 重试生成新 attempt 并进入队尾。
- 单个任务异常后 Worker 继续消费。
- 模拟 processing 状态退出后，重新启动会自动恢复为 queued。
- 恢复不会创建重复活动 Job。
- staging 半成品和孤立文件被安全清理，已完成产物不受影响。

### 12.3 API 集成测试

- 上传与 URL 两种创建形式。
- 通过缩小测试限制模拟超限，不创建真实 5GB 文件。
- Dashboard、详情、转录、摘要、章节与导出契约。
- cancel、retry 和独立 regenerate Job。
- Provider 完整 CRUD、连接测试和密钥不回显。
- Profile 修改后，已排队任务仍使用旧 revision。
- 所有验证异常使用统一错误结构。
- 媒体 Range 请求返回 206 和正确 Content-Range。
- 现有 1.x 测试与新增兼容测试全部通过。

默认自动测试不调用真实 Provider、Whisper 或大媒体。Pipeline 通过依赖注入使用 Fake；另提供可选
的本地 smoke test，以短音频、FFmpeg 和测试 Provider 验证完整链路。

## 13. 分阶段实施顺序

1. 建立 1.x 回归基线，并拆出 v2 应用装配边界。
2. 增加启动配置、SQLite 初始化和版本化 migration。
3. 实现 Provider Profile 加密存储及完整 API。
4. 实现 Episode、Transcript、Summary、Chapter Repository 与只读 API。
5. 实现持久 Job、FIFO Scheduler、Worker 并发限制和启动恢复。
6. 接入上传与 URL 管线、进度、取消、失败降级和自动章节。
7. 实现 Dashboard、媒体服务、导出、重试及独立重新生成任务。
8. 完成安全测试、并发测试、1.x 全量回归、Docker 与 API 文档更新。

详细实施计划必须在用户审阅并批准本文档后另行编写。实施阶段采用 TDD，每个任务以失败测试、
最小实现、验证和独立提交为一个闭环。

## 14. 验收标准

- 默认并发为 2，提交任意数量项目均不会超过配置上限。
- 队列可跨服务重启恢复，任务不丢失且不被重复领取。
- FIFO、取消、重试和 queuePosition 符合本文状态机。
- Profile 修改不影响已排队任务。
- API Key 不以明文落库，不出现在响应、日志或 Job 中。
- 转录成功但 AI 附加阶段失败时，项目仍可使用并返回明确 warnings。
- v2 响应符合 camelCase 和统一错误契约。
- 现有 1.x 测试和新增兼容测试全部通过。
- 单进程生产启动、Docker 启动和优雅关闭均经过验证。

## 15. 明确不做

- 不迁移或自动导入 1.x 历史项目。
- 不引入 Redis、Celery 或其他外部任务队列。
- 不支持多实例或多 Uvicorn worker 协同消费。
- 不实现阶段级断点续传；中断任务从管线开头重跑。
- 不自动过期或清理成功、失败项目的源文件。
- 不在 v2 API 中暴露磁盘路径、明文凭据或上游原始错误。
- 不在本设计之外重写 1.x 前端或无关后端模块。
