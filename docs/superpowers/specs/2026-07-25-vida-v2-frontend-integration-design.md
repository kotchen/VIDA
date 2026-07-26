# VIDA 2.0 前后端适配设计

- 日期：2026-07-25
- 状态：已获用户批准，待书面复核
- 后端契约：[`docs/api/v2-api-contract.md`](../../api/v2-api-contract.md)
- 新版应用路径：`/v2`
- 新版 API 路径：`/api/v2`

## 1. 背景

VIDA 2.0 React 前端已完成 Dashboard 的视觉实现和路由骨架，但当前仍通过
`MockProvider` 读取静态数据。上传、播放器、导出、章节操作等控件尚未连接真实业务。

VIDA 2.0 后端已经提供 Episode、Job、Dashboard、Provider Profile、媒体、章节和导出 API。
这次适配需要让新版前端覆盖完整 v2 能力，同时保持 VIDA 1.x 的根页面、静态资源和旧 API
行为不变。

本次不是简单替换数据源。前端还需要正确处理空数据、五种 Episode 状态、任务进度、队列位置、
取消、重试、重新生成、Provider 凭据、媒体播放、文件下载和统一错误结构。为避免依赖高频轮询，
后端还需要补充一个 v2 SSE 事件通道。

## 2. 目标与范围

### 2.1 目标

1. 将 React 前端从 Mock 数据切换到 `/api/v2`。
2. 实现 Dashboard、Transcribe、Library、Episode Detail、Summaries 和 Settings 六类页面。
3. 支持文件与 URL 提交、实时任务状态、取消、重试和内容重新生成。
4. 支持 Provider Profile 完整管理与连接测试。
5. 支持媒体播放、转录、摘要、章节编辑、书签和 TXT/SRT/Markdown 导出。
6. 由 FastAPI 在 `/v2` 托管生产构建，开发环境由 Vite 代理 API。
7. 保持 VIDA 1.x 的 `/`、`/static/*` 和 `/api/*` 兼容。

### 2.2 本次新增的后端能力

现有契约缺少三个前端需要的写能力，本次一起补齐：

1. `GET /api/v2/events`：v2 SSE 通知通道。
2. `DELETE /api/v2/episodes/{episodeId}`：删除终态 Episode 及其产物。
3. `PATCH /api/v2/episodes/{episodeId}/chapters/{chapterId}` 支持
   `bookmarked` 字段。

### 2.3 明确不做

- 不迁移 VIDA 1.x 历史任务或 `temp/` 数据。
- 不增加用户、登录、权限或多租户系统。
- 不增加 Episode 批量操作。
- 不增加全文服务端搜索；当前列表筛选和搜索在已加载数据内完成。
- 不增加新的服务端 Summary 列表聚合接口；Summaries 页面按用户选择加载单个 Summary。
- 不使用 WebSocket；服务端只需单向发送状态失效通知。

## 3. 已确认的产品决策

| 决策点 | 结论 |
|---|---|
| 功能范围 | 覆盖 v2 全部现有能力，并补齐 SSE、Episode 删除和章节书签 |
| 前端状态方案 | 功能模块化 API + React 原生状态和 hooks |
| 新依赖 | 不引入全局状态库或 TanStack Query |
| 生产部署 | FastAPI 与 React 单端口同源 |
| 旧版兼容 | 1.x 继续使用 `/` |
| 新版路径 | React 应用统一挂载在 `/v2` |
| API 路径 | 保持 `/api/v2` |
| 项目详情 | 新增 `/v2/episodes/:episodeId` |
| 提交来源 | Dashboard 快速文件上传；Transcribe 支持文件和 URL |
| 实时更新 | 单 SSE 连接通知失效，REST 回读为事实来源 |
| SSE 降级 | 断线自动重连，并以 30 秒轮询兜底 |
| 活动项目删除 | queued/processing 必须先取消，变为 canceled 后才能删除 |
| 章节书签 | generated 和 manual 章节均可修改 bookmarked |
| 章节内容编辑 | 仅 manual 章节可修改时间、标题或被删除 |

## 4. 总体架构

采用“模块化 REST 客户端 + 页面级 hooks + 全局 SSE 失效通知”。

```text
React pages/components
        │
        ├── feature hooks ────── local UI/loading/mutation state
        │
        ├── typed API modules ── REST reads and writes
        │
        └── event context ────── one EventSource for /api/v2/events
                                      │
                                      └── invalidate relevant REST reads

FastAPI /api/v2
        ├── existing API routers/services/repositories
        ├── V2EventBroker
        └── SSE router

SQLite and data/v2 remain the source of truth.
```

SSE payload 不作为最终业务数据。收到事件后，前端重新请求相关 REST 资源。这保证事件丢失、
连接重建或服务重启时仍能通过一次回读恢复一致状态。

### 4.1 前端目录边界

```text
frontend/src/
├── api/
│   ├── client.ts              # fetch、错误解析、request ID、空响应
│   ├── episodes.ts            # Episode、Dashboard、章节、导出和任务操作
│   ├── profiles.ts            # Provider Profile CRUD 和连接测试
│   ├── events.ts              # EventSource 生命周期与事件解析
│   └── types.ts               # 与 v2 camelCase 响应严格一致
├── features/
│   ├── dashboard/             # Dashboard 查询和快速上传
│   ├── submission/            # 文件/URL 表单与上传进度
│   ├── episode/               # 详情读取、任务控制和内容操作
│   ├── library/               # 分页项目列表与筛选
│   └── profiles/              # Profile 表单、偏好和连接测试
├── pages/
│   ├── DashboardPage.tsx
│   ├── TranscribePage.tsx
│   ├── LibraryPage.tsx
│   ├── EpisodePage.tsx
│   ├── SummariesPage.tsx
│   └── SettingsPage.tsx
└── test/
```

页面和展示组件不直接调用裸 `fetch`。`api/` 负责协议，`features/` 负责业务状态和动作，
components 只接收数据与回调。

### 4.2 数据模型修正

现有 `frontend/src/data/types.ts` 与真实契约有以下差异，适配时必须修正：

- Episode 状态增加 `queued` 和 `canceled`。
- Episode 增加 `progress`、`message`、`queuePosition`、`providerProfileId` 和 `warnings`。
- `mediaUrl`、`posterUrl`、`resolution`、Chapter thumbnail 和 Project thumbnail 支持 `null`。
- Dashboard 的 `currentEpisode` 与 `summary` 支持 `null`。
- Job 和 Provider Profile 使用完整 v2 响应模型。
- `generatedBy` 收窄为后端契约值 `VIDA`。

Mock 数据继续作为测试 fixture 使用，但生产运行时不再实例化 `MockProvider`。

## 5. 路由与页面职责

React Router 使用 `basename="/v2"`。

### 5.1 Dashboard：`/v2/dashboard`

- 请求 `GET /api/v2/dashboard`。
- 展示最近完成 Episode 的媒体、摘要、转录和章节。
- 展示最近 12 个项目，包括五种状态、进度和队列位置。
- 没有完成项目时显示引导型空状态，不渲染需要非空数据的卡片。
- 保留快速文件上传；提交前选择 Provider Profile 和摘要语言。
- 最近项目点击后进入 Episode Detail。
- 收到 Dashboard 或相关 Episode 事件后刷新聚合数据。

### 5.2 Transcribe：`/v2/transcribe`

- 提供文件与 URL 两种互斥来源。
- 公共字段为可选标题、Provider Profile 和摘要语言。
- 文件上传使用 `XMLHttpRequest` 获取浏览器到服务器的真实上传进度。
- URL 提交和其他 JSON 请求使用统一 `fetch` 客户端。
- 没有可用 Profile 时禁用提交并引导到 Settings。
- `202` 成功后立即跳转 `/v2/episodes/{id}`。

### 5.3 Library：`/v2/library`

- 使用 `limit`、`offset` 分页读取 Episode 列表。
- 支持前端状态筛选和当前已加载项目内的标题搜索。
- 项目卡展示缩略图、状态、进度、创建时间和时长。
- queued/processing 项目提供取消入口。
- completed/failed/canceled 项目提供删除入口，并要求二次确认。
- 删除后刷新 Library、Dashboard 和 Summaries 数据。

### 5.4 Episode Detail：`/v2/episodes/:episodeId`

页面根据 Episode 状态切换内容：

- `queued`：进度、message、queuePosition 和取消。
- `processing`：进度、message 和取消。
- `failed`：安全错误说明、request ID（如果来自当前操作）和重试。
- `canceled`：重试或删除。
- `completed`：媒体、转录、摘要、章节、warnings 和导出。

completed 状态支持：

- 使用受控 media URL 和原生媒体能力播放，保留服务端 Range/ETag 行为。
- 读取转录、摘要和章节。
- 重新生成摘要与 generated 章节。
- 新增、编辑和删除 manual 章节。
- 切换任意章节的 bookmarked。
- 下载 TXT、SRT 和 Markdown。
- 删除 Episode，成功后返回 Library。

内容请求按状态触发，queued/processing 不提前请求尚未生成的 Summary。

### 5.5 Summaries：`/v2/summaries`

- 展示已加载的 completed 项目列表。
- 用户选中项目后再请求 `/episodes/{id}/summary`，避免首屏 N+1。
- 支持进入 Episode Detail 和重新生成当前 Summary。
- `summary_not_found` 显示可恢复空状态，不把整个页面视为失败。

### 5.6 Settings：`/v2/settings`

- Provider Profile 列表、创建、编辑、删除和连接测试。
- 创建时 API Key 必填。
- 编辑时省略或留空 API Key 表示继续使用旧凭据；前端不尝试回显密钥。
- 删除前说明其对历史与已入队任务没有影响，但不能再用于新任务。
- 记住最后一次选择的 Provider Profile 和摘要语言。
- 已选择 Profile 被删除后清除本地选择。

## 6. SSE 设计

### 6.1 服务端端点

```http
GET /api/v2/events
Accept: text/event-stream
```

响应要求：

- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- 禁止代理缓冲所需的响应头
- 建议客户端重连间隔 3 秒
- 每 15 秒发送 heartbeat

第一版为内存事件 broker，符合现有“单 FastAPI/Uvicorn 进程”约束。它不提供跨进程分发，
也不持久化或回放事件。

### 6.2 事件类型

```ts
type V2Event =
  | {
      type: "episode.updated"
      episodeId: string
      status: EpisodeStatus
      progress: number
    }
  | {
      type: "episode.deleted"
      episodeId: string
    }
  | {
      type: "job.updated"
      jobId: string
      episodeId: string
      status: EpisodeStatus
      progress: number
    }
  | {
      type: "dashboard.invalidated"
    }
  | {
      type: "profiles.invalidated"
    }
  | {
      type: "heartbeat"
    }
```

事件带进程内递增 ID。新连接先收到 ready/heartbeat，客户端随后执行当前页面的 REST 刷新。
断线重连也执行同样刷新，因此不依赖事件回放。

### 6.3 发布边界

事件只能在数据库事务成功提交后发布：

- Episode 提交、取消、重试、删除。
- Job 领取、进度变化、完成、失败或取消。
- Summary/Chapter 重新生成开始与结束。
- Chapter 新增、编辑、删除或书签变化。
- Provider Profile 创建、更新或删除。

事件不得包含 API Key、密文、来源 URL、上游响应、原始异常或文件系统路径。

每个 SSE 连接使用有界队列。慢客户端允许丢弃较旧的失效通知，不能反向阻塞 Worker；
客户端后续 REST 回读会恢复最新状态。

### 6.4 前端连接策略

- `AppShell` 下只创建一个 `EventSource`。
- 页面或 feature hook 按资源 ID 订阅内存事件分发器。
- 浏览器隐藏时保持 SSE，但停止 30 秒兜底轮询。
- SSE `open` 或重连后刷新当前页面资源。
- SSE 断线不禁用写操作；顶栏显示轻量离线提示。
- SSE 不可用期间，每 30 秒刷新活跃 Episode、当前列表或 Dashboard。

## 7. 新增后端写契约

### 7.1 删除 Episode

```http
DELETE /api/v2/episodes/{episodeId}
```

- 只接受 `completed`、`failed` 或 `canceled`。
- `queued` 或 `processing` 返回 `409 invalid_episode_state`。
- 不存在返回 `404 episode_not_found`。
- 成功删除 Episode、关联 Job、Transcript、Summary、Chapter 和产物索引。
- 数据库提交后清理 `data/v2/episodes/{episodeId}`。
- 文件清理失败不得恢复已删除数据库记录；记录安全日志，并由启动 orphan 清理兜底。
- 成功返回 `204 No Content`，随后发布 `episode.deleted` 和
  `dashboard.invalidated`。

### 7.2 更新章节书签

现有 PATCH 请求增加可选字段：

```json
{
  "startSec": 123,
  "title": "Chapter title",
  "bookmarked": true
}
```

规则：

- 请求至少包含 `startSec`、`title`、`bookmarked` 之一，字段不能是 `null`。
- 任意章节都可以更新 `bookmarked`。
- 只有 manual 章节可以更新 `startSec` 或 `title`。
- generated 章节更新内容仍返回 `409 generated_chapter_immutable`。
- 成功返回更新后的 Chapter，并发布 Episode 失效事件。

## 8. 错误处理

统一前端错误类型：

```ts
interface ApiError {
  httpStatus: number
  code: string
  message: string
  details: Record<string, unknown>
  requestId: string | null
}
```

处理规则：

- 每个 API 响应读取 `X-Request-ID`，错误 body 中的 request ID 作为后备。
- 页面展示用户可理解的消息，并允许复制 request ID。
- `404 episode_not_found`：详情页提示已删除并返回 Library。
- `409`：刷新资源后解释当前状态冲突，不保留乐观假状态。
- `422`：映射到表单或 Provider 不可用提示。
- `413`：提示上传超过服务端配置上限。
- `415`：提示允许的媒体扩展名。
- 网络错误：保留当前已加载数据，显示可重试通知。
- Dashboard nullable 字段、图片 null、Summary 404 和空章节都是正常可渲染状态。

客户端不显示上游原始错误、凭据或可能包含敏感查询参数的 URL。

## 9. 部署

### 9.1 开发环境

- Vite 监听 `7100`。
- `base` 与 Router basename 均为 `/v2/`。
- Vite 将 `/api` 代理到 FastAPI `8000`。
- 前端始终使用相对 `/api/v2` URL，不引入环境相关绝对主机地址。

### 9.2 生产环境

Docker 使用多阶段构建：

1. Node 构建阶段安装 frontend lockfile 依赖并生成 `frontend/dist`。
2. Python 运行阶段只复制构建产物，不携带 Node 工具链。
3. FastAPI 在 `/v2/assets/*` 提供构建资源。
4. `/v2` 和 `/v2/{clientRoute}` 返回 React `index.html`。

路由安装顺序必须保证：

- `/api/v2/*` 始终由 API router 处理。
- `/static/*` 和 `/` 继续使用 1.x 文件。
- `/v2/*` SPA fallback 不吞掉 API 或旧静态资源。

生产仍只运行一个 Uvicorn 进程，内部 Worker 数由 v2 配置控制。

## 10. 测试策略

### 10.1 后端

- SSE 连接、heartbeat、事件格式、事务提交后发布和断开清理。
- 慢订阅者队列不会阻塞 Job。
- Job 从 queued 到 processing、completed/failed/canceled 的事件覆盖。
- Episode 三种终态删除成功，活动状态删除返回 409。
- 删除级联数据库记录并清理文件；文件残留可由 orphan 清理恢复。
- generated/manual 章节书签更新。
- generated 章节内容仍不可修改或删除。
- 所有新增错误继续符合 v2 error contract 并包含 request ID。
- `/`、`/static/*` 和旧 `/api/*` 回归测试继续通过。

### 10.2 前端

- API client：JSON、204、下载、错误结构、request ID 和网络错误。
- Event client：解析、单连接、订阅取消、重连刷新和错误降级。
- Dashboard：有数据、无完成项目和请求失败。
- Transcribe：文件/URL 表单、Profile 缺失、上传进度和跳转。
- Library：分页、状态筛选、取消、删除确认和事件刷新。
- Episode Detail：五种状态、内容加载、重试、再生成、章节操作和导出。
- Settings：Profile CRUD、密钥不回显、连接测试和本地选择失效。
- Summaries：按选择加载，Summary 404 空状态。
- Router basename 和 `/v2/*` 页面导航。

### 10.3 集成验收

- 安装锁定依赖后，frontend lint、Vitest 和 production build 全部通过。
- 后端 v2 与 legacy pytest 全部通过。
- FastAPI 单端口同时正确提供 `/`、`/v2` 和 `/api/v2`。
- Docker 中完成 Profile 创建、上传、SSE 进度、播放、导出、删除的冒烟流程。
- SSE 被主动中断后，页面自动重连或通过 30 秒轮询恢复正确状态。

## 11. 分阶段实施

### 阶段 1：契约与后端补充

- 更新 v2 API contract。
- 实现 V2EventBroker、SSE router、Episode 删除和章节书签。
- 完成后端单元与 API 测试。

### 阶段 2：前端基础设施与部署

- 建立严格 API types、统一客户端、错误模型和 event client。
- 配置 `/v2` basename、开发代理和生产 SPA 托管。
- 保持 Mock 数据仅作为测试 fixture。

### 阶段 3：Settings / Provider Profile

- 完成 Profile CRUD、连接测试、密钥沿用和默认选择。
- 为提交功能提供稳定 Profile 依赖。

### 阶段 4：Transcribe / 任务生命周期

- 完成文件与 URL 提交。
- 完成上传进度、SSE 处理进度、队列状态、取消和重试。

### 阶段 5：Episode Detail

- 完成媒体、转录、摘要、章节和导出。
- 完成章节 CRUD/书签、内容重新生成和 warnings。

### 阶段 6：Dashboard / Library / Summaries

- 替换 Dashboard MockProvider。
- 完成列表、分页、筛选、删除和跨页面实时失效。

### 阶段 7：硬化与发布验收

- 运行完整前后端测试、构建和 Docker 冒烟。
- 验证 SSE 降级、敏感信息安全和 1.x 兼容。

每个阶段结束时都必须产生可独立测试的工作软件，不能依赖后续阶段才能验证本阶段的接口或行为。

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| SSE 事件遗漏或服务重启 | 事件只做失效通知；连接后 REST 回读；30 秒轮询兜底 |
| 慢客户端阻塞 Worker | 每连接有界队列，丢弃旧通知，不在 Worker 内等待客户端 |
| 删除时数据库与文件不一致 | 数据库先提交；文件失败由安全日志和启动 orphan 清理兜底 |
| Dashboard 假设数据非空 | 类型层强制 nullable，页面有明确空状态 |
| 上传进度与处理进度混淆 | UI 分成“上传到服务器”和“服务端处理”两个阶段 |
| Provider API Key 泄漏 | 只发送用户主动输入的新 key；不回显、不写日志、不进入 SSE |
| 新版 SPA 破坏旧版根路由 | 新版限定 `/v2`；增加 legacy 路由回归测试 |
| 当前依赖未安装，未知构建基线 | 实施第一步安装 lockfile 依赖并记录 lint/test/build 基线 |

