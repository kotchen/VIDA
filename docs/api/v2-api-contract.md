# VIDA 2.0 后端 API 契约

- 日期：2026-07-18
- 状态：待后端实施（交接文档）
- 消费方：VIDA 2.0 React 前端（`frontend/`，当前以 MockProvider 实现相同结构）
- 关联文档：[VIDA 2.0 Dashboard UI 设计文档](../superpowers/specs/2026-07-18-vida-2-dashboard-ui-design.md)

本文档定义 VIDA 2.0 前端所需的全部后端接口。实施时请在现有 FastAPI（`backend/main.py`）
基础上新增，**不要破坏 1.x 前端（`static/`）正在使用的现有接口**。

## 全局约定

| 项 | 约定 |
|---|---|
| 基础路径 | `/api/v2` |
| 数据格式 | JSON，字段名 **camelCase** |
| 时间 | ISO 8601 字符串（如 `2026-07-18T14:00:00Z`） |
| 时长 | 数字，单位**秒**（字段名以 `Sec` 结尾，如 `durationSec`） |
| 枚举 | 小写字符串（见各模型） |
| 错误 | 统一结构 `{ "error": { "code": "...", "message": "..." } }` + 合适的 HTTP 状态码 |
| 媒体/图片 URL | 后端可访问的静态资源 URL（相对或绝对均可，前端不做跨域假设） |

## 数据模型

```ts
interface Episode {
  id: string
  title: string
  sourceType: 'upload' | 'url'
  mediaUrl: string              // 可播放的媒体地址
  posterUrl: string             // 封面图地址
  durationSec: number
  resolution?: string           // 如 "1080p"，纯音频可省略
  status: 'completed' | 'processing' | 'failed'
  language: string              // 如 "en"、"zh"
  createdAt: string             // ISO 8601
}

interface TranscriptSegment {
  id: string
  startSec: number
  endSec: number
  speaker: string               // 说话人名，无识别结果时给 "Speaker 1" 等兜底
  text: string
}

interface Chapter {
  id: string
  startSec: number
  title: string
  durationSec: number
  thumbnailUrl: string
  bookmarked: boolean
}

interface Summary {
  episodeId: string
  content: string               // 摘要正文（纯文本段落）
  readTimeMin: number
  keyPoints: number
  confidence: number            // 0-100 的整数百分比，如 92
  generatedBy: string           // 展示用，如 "VIDA"
}

interface Project {
  id: string
  title: string
  createdAt: string
  durationSec: number
  status: 'completed' | 'processing' | 'failed'
  thumbnailUrl: string
}

interface DashboardData {
  currentEpisode: Episode
  summary: Summary
  transcript: TranscriptSegment[]
  chapters: Chapter[]
  recentProjects: Project[]
}
```

## 接口列表

### 1. 聚合首屏数据（前端首屏唯一必需接口）

```
GET /api/v2/dashboard
```

返回 `DashboardData`。`currentEpisode` 为"当前项目"：取用户最近一个
`status = 'completed'` 的 Episode；一个项目都没有时返回：

```json
{ "currentEpisode": null, "summary": null, "transcript": [], "chapters": [], "recentProjects": [] }
```

（前端对空态另行设计，不在本契约版本内。）

### 2. 项目列表

```
GET /api/v2/episodes?limit=12&offset=0
```

返回 `Project[]`，按 `createdAt` 倒序。`limit` 默认 12，最大 100。

### 3. 单集详情

```
GET /api/v2/episodes/{id}
```

返回 `Episode`；不存在返回 404 `{ "error": { "code": "episode_not_found", ... } }`。

### 4. 转录分段

```
GET /api/v2/episodes/{id}/transcript
```

返回 `TranscriptSegment[]`，按 `startSec` 升序。

### 5. AI 摘要

```
GET /api/v2/episodes/{id}/summary
```

返回 `Summary`；摘要尚未生成返回 404 `{ "error": { "code": "summary_not_found", ... } }`。

### 6. 章节

```
GET /api/v2/episodes/{id}/chapters
```

返回 `Chapter[]`，按 `startSec` 升序。

```
POST /api/v2/episodes/{id}/chapters
Content-Type: application/json

{ "startSec": 123, "title": "New Chapter" }
```

返回创建后的 `Chapter`（`thumbnailUrl`、`durationSec`、`bookmarked=false` 由后端补全）。

### 7. 导出

```
GET /api/v2/episodes/{id}/export?format=txt|srt|md
```

返回文件下载（`Content-Disposition: attachment`）：

| format | 内容 | Content-Type |
|---|---|---|
| `txt` | 转录纯文本（含说话人与时间戳） | `text/plain; charset=utf-8` |
| `srt` | 标准 SRT 字幕 | `application/x-subrip` |
| `md` | Markdown：标题 + 摘要 + 章节 + 转录全文 | `text/markdown; charset=utf-8` |

非法 format 返回 400 `{ "error": { "code": "invalid_format", ... } }`。

### 8. 上传创建（异步转录）

```
POST /api/v2/episodes
Content-Type: multipart/form-data

file: <音视频或 txt 文件>     # MP4, MOV, MKV, AVI, MP3, M4A 等，上限 5GB
```

返回 `202 Accepted` + `Episode`（`status: 'processing'`）。处理完成后同一 id 的
`status` 变为 `completed`（前端轮询 `GET /api/v2/episodes/{id}` 实现，本契约版本不含推送）。

## 与现有 1.x 后端的映射建议（供实施参考）

- 现有"按标题文件夹组织的生成文件库"可作为 `Episode`/`Project` 的数据来源；
  文件夹名 → `title`，音频/视频文件 → `mediaUrl`，转录文本 → `transcript` 的数据源
- 现有转录文本若**无说话人与逐段时间戳**，可先整段返回单条 `TranscriptSegment`
  （`speaker: "Transcript"`，`startSec: 0`），SRT 生成同理可先粗粒度
- `Summary` 的 `readTimeMin` / `keyPoints` / `confidence` 为新概念，后端可先给
  基于字数的估算值（如 `readTimeMin = ceil(字数 / 400)`）
- `Chapter` 为全新概念，可由 LLM 基于转录文本分段生成，或先返回空数组

## 版本说明

- v2 契约第一版：只读为主，写操作仅"新增章节"与"上传"
- 用户系统、鉴权、通知、订阅计划**均不在本版范围**
