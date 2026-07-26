# VIDA 2.0 后端 API 契约

- 日期：2026-07-20
- 状态：已实施；本文件与 `backend/v2/` 和 OpenAPI 保持一致
- 基础路径：`/api/v2`
- 兼容性：现有 VIDA 1.x 的 `/`、`/static/*` 和 `/api/*` 行为保持不变

## 通用约定

- JSON 字段使用 `camelCase`；时间是 UTC ISO 8601 字符串；所有 `*Sec` 字段单位为秒。
- Episode 和 Job 的公开状态都是 `queued | processing | completed | failed | canceled`。
- `progress` 是 0–100 的整数；`queuePosition` 从 1 开始，仅 queued Job/Episode 有值，其他状态为 `null`。
- 创建 Episode 返回 `202 Accepted` 和 `Location: /api/v2/episodes/{id}`。
- 所有 v2 响应都包含 `X-Request-ID`。客户端传入 `X-Request-ID` 时沿用该值，否则服务端生成 UUID。
- v2 不读取、迁移或修改 `temp/` 和 `temp/tasks.json`。

错误响应统一为：

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {},
    "requestId": "uuid"
  }
}
```

## 实时事件流

```http
GET /api/v2/events
Accept: text/event-stream
```

成功响应使用 `Content-Type: text/event-stream`，并返回
`Cache-Control: no-cache` 与 `X-Accel-Buffering: no`。连接建立后先发送
`retry: 3000`；事件帧包含递增的 `id`、`event` 和单行 JSON `data`，空闲时每
15 秒发送一次 heartbeat comment。

公开事件类型和 data 如下：

```ts
type V2Event =
  | { type: 'episode.updated'; data: {
      episodeId: string; status: EpisodeStatus; progress: number
    } }
  | { type: 'episode.deleted'; data: { episodeId: string } }
  | { type: 'job.updated'; data: {
      jobId: string; episodeId: string; status: EpisodeStatus; progress: number
    } }
  | { type: 'profiles.invalidated'; data: Record<string, never> }
  | { type: 'dashboard.invalidated'; data: Record<string, never> }
```

事件只用于提示客户端重新读取 REST 资源，不替代 REST 作为状态真值。事件
broker 位于单个服务进程内，不持久化、无跨进程广播，也不支持 replay；
`Last-Event-ID` 不保证补发。断线重连后客户端必须重新获取当前页面依赖的
Episode、Job、Profile 或 Dashboard 数据。生产部署仍只允许单个
FastAPI/Uvicorn 进程。

## 响应模型

```ts
type EpisodeStatus = 'queued' | 'processing' | 'completed' | 'failed' | 'canceled'
type JobType = 'process_episode' | 'regenerate_summary' | 'regenerate_chapters'

interface ProcessingWarning {
  stage: 'optimization' | 'summary' | 'chapters'
  code: string
  message: string
}

interface Episode {
  id: string
  title: string
  sourceType: 'upload' | 'url'
  sourceUrl: string | null
  mediaUrl: string | null
  posterUrl: string | null
  durationSec: number
  resolution: string | null
  status: EpisodeStatus
  language: string
  createdAt: string
  progress: number
  message: string
  queuePosition: number | null
  providerProfileId: string
  warnings: ProcessingWarning[]
}

interface Project {
  id: string
  title: string
  createdAt: string
  durationSec: number
  status: EpisodeStatus
  thumbnailUrl: string | null
}

interface TranscriptSegment {
  id: string
  startSec: number
  endSec: number
  speaker: string
  text: string
}

interface Summary {
  episodeId: string
  content: string
  readTimeMin: number
  keyPoints: number
  confidence: number // 0–100 整数
  generatedBy: 'VIDA'
}

interface Chapter {
  id: string
  startSec: number
  title: string
  durationSec: number
  thumbnailUrl: string | null
  bookmarked: boolean
  source: 'generated' | 'manual'
}

interface Job {
  id: string
  episodeId: string
  type: JobType
  attempt: number
  status: EpisodeStatus
  providerProfileRevisionId: string
  submittedAt: string
  startedAt: string | null
  finishedAt: string | null
  progress: number
  message: string
  queuePosition: number | null
  errorCode: string | null
  errorMessage: string | null
}
```

## Provider Profile

Provider 凭据只通过以下 API 管理。API Key 使用 AES-256-GCM 加密保存，响应、Job、日志和异常中均不返回明文。

```text
POST /api/v2/provider-profiles
GET /api/v2/provider-profiles
GET /api/v2/provider-profiles/{id}
PATCH /api/v2/provider-profiles/{id}
DELETE /api/v2/provider-profiles/{id}
POST /api/v2/provider-profiles/models
POST /api/v2/provider-profiles/{id}/test
```

创建请求：

```json
{
  "name": "Primary",
  "baseUrl": "https://api.example/v1",
  "apiKey": "secret",
  "modelId": "model-id",
  "temperature": 0.1
}
```

`name`、`baseUrl`、`apiKey`、`modelId` 必填且非空；`temperature` 范围为 0–2。创建返回 201：

```json
{
  "id": "uuid",
  "name": "Primary",
  "baseUrl": "https://api.example/v1",
  "modelId": "model-id",
  "temperature": 0.1,
  "revision": 1,
  "activeRevisionId": "uuid",
  "apiKeyMasked": "•••••••cret",
  "hasApiKey": true,
  "createdAt": "2026-07-20T00:00:00Z",
  "updatedAt": "2026-07-20T00:00:00Z"
}
```

PATCH 至少包含一个非 null 字段。省略 `apiKey` 会沿用旧凭据；空字符串不能用于清空。每次有效更新创建不可变的新 revision，已入队 Job 继续使用其提交时固定的 `providerProfileRevisionId`。DELETE 返回 204 并逻辑删除 Profile；它不影响历史或已入队 Job，但新提交不能再引用它。列表和详情不返回已删除 Profile。

连接测试成功返回 `{ "ok": true, "latencyMs": 15, "modelAvailable": true, "message": "Connection successful" }`；上游原始响应、请求头、密钥和查询参数不会透传。

### 模型发现

Profile 创建或更新前可使用草稿凭据发现 OpenAI-compatible Provider 的模型：

```http
POST /api/v2/provider-profiles/models
Content-Type: application/json
```

```json
{
  "profileId": "optional-existing-profile-id",
  "baseUrl": "https://api.example/v1",
  "apiKey": "optional-draft-api-key"
}
```

`baseUrl` 必填且必须是 HTTP(S) URL。新建模式必须提供 `apiKey`；编辑模式提供
`profileId` 后可省略 `apiKey`，服务端会使用该 Profile active revision 的已加密保存密钥。
同时提供两者时，草稿 `apiKey` 优先。即使使用已保存密钥，`baseUrl` 仍取本次请求值，
便于保存前测试新地址。未知字段、空 API Key，或同时缺少 `profileId`/`apiKey` 返回
422 `validation_error`；不存在或已删除的 Profile 返回 404
`provider_profile_not_found`。

成功响应：

```json
{
  "models": [
    {
      "id": "model-a",
      "name": "Model A"
    }
  ],
  "latencyMs": 24
}
```

模型按 ID 去重并做大小写不敏感排序；空 ID、超过 512 字符的 ID/名称被忽略，
名称缺失时使用 ID，最多返回 2000 个唯一模型。上游请求总超时为 15 秒。
模型发现不保存 Base URL、API Key 或结果，也不创建 Profile revision。

连接失败、鉴权失败、超时或上游响应异常统一返回 502
`provider_models_fetch_failed`。响应和日志不会包含 API Key、完整 URL query、上游响应
body、header 或原始 SDK 异常。

## Episode 提交、列表和读取

### URL 提交

```http
POST /api/v2/episodes
Content-Type: application/json

{
  "sourceUrl": "https://example.com/media.mp3",
  "providerProfileId": "uuid",
  "summaryLanguage": "zh",
  "title": "optional"
}
```

JSON body 最大 16 KiB；未知字段被拒绝。`sourceUrl` 必须是最长 2048 字符的 HTTP(S) URL。下载器拒绝凭据 URL、私网/回环/链路本地目标、DNS rebinding、非标准 Web 端口和不安全重定向。

省略 `title` 时初始标题为 URL 的 hostname；平台页面（YouTube、Bilibili、TikTok、SoundCloud）在来源采集阶段通过 yt-dlp 元数据解析出真实标题后，会在转录开始前替换该回退标题并用于后续摘要生成。用户显式提供的 `title` 永远不会被覆盖。`sourceUrl` 会在创建和详情响应中原样返回，客户端可用它为支持的来源渲染网络播放器。

### 上传提交

```http
POST /api/v2/episodes
Content-Type: multipart/form-data

file=<binary>
providerProfileId=<uuid>
summaryLanguage=<language>
title=<optional>
```

支持 `.mp3 .mp4 .m4a .wav .webm .mkv .ogg .flac`。上传流先写 `.part`，完整校验后原子改名，再在一个事务中创建 Episode 和首个 Job。空文件返回 400，超限返回 413，不支持的扩展名返回 415；超限、断开或解析失败不会留下 Episode、Job 或 `.part`。上限由 `V2_UPLOAD_MAX_GB` 控制，默认 5 GiB。

### 查询

```text
GET /api/v2/episodes?limit=12&offset=0
GET /api/v2/episodes/{id}
GET /api/v2/episodes/{id}/transcript
GET /api/v2/episodes/{id}/summary
GET /api/v2/episodes/{id}/chapters
GET /api/v2/dashboard
```

`limit` 范围 1–100，默认 12；`offset` 最小为 0。列表按 `createdAt`、`id` 倒序。Dashboard 返回最近完成的 Episode 及其 Summary、Transcript、Chapters，并返回最近 12 个项目；没有已完成项目时相关字段为 `null`/空数组。Summary 未生成时返回 `summary_not_found`。

转录按 `startSec` 排序；空 speaker 以 `Speaker 1` 返回。章节按 `startSec` 排序，`durationSec` 由下一章节起点或 Episode 总时长推导。

### 删除

```text
DELETE /api/v2/episodes/{id}
```

只有 `completed`、`failed` 或 `canceled` Episode 可以删除，成功返回 204。
删除会在数据库事务中级联移除关联 Job、转录、摘要和章节，并在事务提交后清理
受控的 `data/v2/episodes/{episodeId}/` 文件树。`queued` 或 `processing`
返回 409 `invalid_episode_state`，调用方应先取消；不存在的 Episode 返回 404
`episode_not_found`。成功后事件流依次发布 `episode.deleted` 和
`dashboard.invalidated`。

## Queue、Job、取消和重试

```text
GET /api/v2/jobs/{jobId}
POST /api/v2/jobs/{jobId}/cancel
POST /api/v2/episodes/{id}/cancel
POST /api/v2/episodes/{id}/retry
```

- Job 使用 SQLite 持久 FIFO；排序为 `submittedAt`，相同时间按 Job ID。最大并发由 `V2_MAX_CONCURRENT_JOBS` 控制，默认 2。
- queued 取消立即返回 200/canceled；processing 取消设置协作标志并返回 202，Episode 在管线安全点变为 canceled。重复取消 canceled Job 返回 200。
- retry 只接受 failed/canceled Episode，返回 202、新 Job、新 ID、递增 `attempt`，并进入队尾。一个 Episode 同时最多有一个 queued/processing Job。
- 启动时，遗留 processing Job 使用原 Job ID 重置为 queued，取消标志被清除，从管线开头重跑；不会创建重复 attempt。
- 转录、来源或核心持久化失败使 Episode failed。优化、摘要或自动章节失败不会阻止完成，而是写入 typed `warnings`。

## 章节编辑和独立重新生成

```text
POST /api/v2/episodes/{id}/chapters
PATCH /api/v2/episodes/{id}/chapters/{chapterId}
DELETE /api/v2/episodes/{id}/chapters/{chapterId}
POST /api/v2/episodes/{id}/summary/regenerate
POST /api/v2/episodes/{id}/chapters/regenerate
```

章节创建 body 为 `{ "startSec": 123, "title": "New Chapter" }`，返回 201。
PATCH 至少提供 `startSec`、`title` 或 `bookmarked` 之一，字段不能为 null；
start 必须是有限非负数且不超过 Episode 时长。generated 和 manual 章节均可
更新 `bookmarked`；只有 manual 章节可修改 `startSec`/`title` 或删除。
包含内容字段和书签字段的 PATCH 在一个数据库事务中执行；若 generated 章节的
内容修改返回 409 `generated_chapter_immutable`，书签也不会部分更新。DELETE
成功返回 204。

独立重新生成仅接受 completed Episode，返回 202 Job，并占用同一 FIFO/并发槽但不把 Episode 改回 processing。摘要在新值完整生成后原子替换，失败保留旧摘要；章节仅替换 generated 章节，manual 章节始终保留，失败时旧 generated 章节也保留。

## 媒体、封面和 HTTP Range

```text
GET /api/v2/episodes/{id}/media
HEAD /api/v2/episodes/{id}/media
GET /api/v2/episodes/{id}/poster
HEAD /api/v2/episodes/{id}/poster
```

路径始终是受控相对 URL，不暴露磁盘位置。正常 GET 返回 200；单个有效 `Range: bytes=...` 返回 206、`Content-Range` 和对应 bytes。支持 `start-end`、`start-`、`-suffix`；多 Range 或不可满足范围返回 416，并带 `Content-Range: bytes */<size>`。响应带 `Accept-Ranges: bytes`、强 ETag、Last-Modified 和准确 Content-Length。

支持 `If-None-Match`/`If-Modified-Since`（304）、`If-Match`/`If-Unmodified-Since`（412）以及 `If-Range`。HEAD 返回与完整 GET 一致的元数据和空 body，并忽略 Range。

## 导出

```text
GET /api/v2/episodes/{id}/export?format=txt|srt|md
```

成功响应均带 `Content-Disposition: attachment`：

| format | Content-Type | 内容 |
|---|---|---|
| `txt` | `text/plain; charset=utf-8` | 标题、元数据和带说话人/时间戳的转录 |
| `srt` | `application/x-subrip` | 标准编号与 `HH:MM:SS,mmm` 时间码 |
| `md` | `text/markdown; charset=utf-8` | 标题、摘要、章节和完整转录 |

其他 format 返回 400 `invalid_format`。文件名经过清洗，不能注入路径或响应头。

## 错误码

| HTTP | code |
|---:|---|
| 400 | `empty_file`, `invalid_format`, `invalid_source` |
| 404 | `chapter_not_found`, `episode_not_found`, `job_not_found`, `media_not_found`, `provider_profile_not_found`, `summary_not_found`, `not_found` |
| 405 | `method_not_allowed` |
| 409 | `generated_chapter_immutable`, `invalid_episode_state`, `invalid_job_state`, `job_already_active` |
| 412 | `precondition_failed` |
| 413 | `file_too_large` |
| 415 | `unsupported_media_type` |
| 416 | `range_not_satisfiable` |
| 422 | `provider_profile_inactive`, `validation_error` |
| 500 | `internal_error` |
| 502 | `provider_connection_failed`, `provider_models_fetch_failed` |

未知异常仅在服务端记录 traceback；客户端只收到通用 `internal_error` 与 request ID。错误、日志和数据库中的上游消息必须先清洗。

## 启动、存储和恢复

| 设置 | 默认值 | 约束 |
|---|---:|---|
| `V2_MAX_CONCURRENT_JOBS` / `--max-concurrent-jobs` | 2 | 正整数 |
| `V2_UPLOAD_MAX_GB` | 5 | 正整数，GiB |
| `VIDA_PROFILE_MASTER_KEY` / `--profile-master-key` | 无 | URL-safe Base64，解码后恰好 32 bytes |

v2 数据目录固定为项目根目录下 `data/v2/`，SQLite 文件为 `data/v2/vida-v2.sqlite3`，Episode 文件位于 `data/v2/episodes/{episodeId}/`。启动顺序为：校验配置、运行幂等 migration、恢复遗留 processing Job、清理未被数据库引用的 staging/orphan 文件、启动 worker。

第一版生产部署只支持一个 FastAPI/Uvicorn 进程；不要使用多个 Uvicorn worker。应用内部 worker 已提供配置的并发。启动日志只记录数据目录、worker 数和上传上限，不记录主密钥、Provider 凭据、密文、URL query 或数据库内容。生产启动命令关闭 Uvicorn access log，应用也禁用可能输出完整请求 URL、query、header 或 Provider payload 的 HTTP/Provider 依赖日志；处理失败时只记录安全阶段名和异常类型，不记录原始异常消息。

必须把整个 `data/v2/` 和 `VIDA_PROFILE_MASTER_KEY` 作为一个恢复单元安全备份。丢失主密钥后，已存 Provider 凭据不可恢复，服务不会降级到明文存储。
