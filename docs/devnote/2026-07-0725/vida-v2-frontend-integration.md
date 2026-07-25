# VIDA v2 前端集成开发记录

日期：2026-07-25  
实现提交：`4e77170` 至 `928c460`  
关联设计：`docs/superpowers/specs/2026-07-25-vida-v2-frontend-integration-design.md`  
关联计划：`docs/superpowers/plans/2026-07-25-vida-v2-frontend-integration.md`

## 1. 开发内容

### 1.1 v2 API 与类型边界

- 新增集中式、强类型的 v2 API client，统一处理 JSON、空响应、请求 ID 和 v2 错误结构。
- 为 Dashboard、Episode、Job、Provider Profile、Summary、Chapter、Export 和 SSE 事件建立
  TypeScript 类型，页面不再直接依赖 mock 数据结构。
- 所有前端请求使用相对路径 `/api/v2/*`；Vite 开发服务器代理到后端 `8000` 端口，生产构建
  由 FastAPI 在 `/v2/` 下同源托管。

### 1.2 Provider Profile 管理

- Settings 页面接入 Profile 列表、创建、编辑、逻辑删除和连接测试。
- API Key 只提交给后端加密保存，前端不回显已保存的明文凭据。
- 将用户选择的默认 Profile 和常用提交偏好保存在浏览器本地，避免把 UI 偏好混入服务端领域模型。

### 1.3 Episode 提交与生命周期

- Transcribe 页面支持 URL、媒体文件和文本文件三类提交，并接入 Profile、模型、语言和处理选项。
- Episode 页面展示 `queued / processing / completed / failed / canceled` 状态、进度、队列位置、
  warning 和错误信息。
- 支持失败任务重试、排队/处理中任务取消，以及终态 Episode 删除。
- 删除 API 只允许 `completed / failed / canceled` Episode；删除前使用确认对话框，成功后刷新相关资源。

### 1.4 实时更新策略

- 后端新增进程内 `V2EventBroker` 和 `/api/v2/events` SSE 端点，在 Episode、Job、Provider Profile
  和内容资源变化后发布轻量事件。
- SSE 事件仅携带资源类型、资源 ID 和版本等失效信息，不复制完整业务对象；REST/SQLite 继续是
  唯一事实来源。
- 前端保持一条共享 `EventSource`，收到事件后按资源重新拉取 REST 数据。
- 对 `queued` 和 `processing` Episode 保留渐进轮询作为降级路径：连接异常、代理中断或事件丢失时
  页面仍会逐步收敛到服务端状态；进入终态后停止高频轮询。

### 1.5 内容操作与页面接入

- Episode 页面接入播放器、转录稿、摘要、章节、导出、摘要重新生成和章节编辑。
- 手工章节书签可创建、更新和删除；后端保持手工章节与生成章节的所有权边界。
- Dashboard、Library、Summaries、Transcribe、Episode 和 Settings 页面全部改为读取 v2 数据。
- React Router 使用 `/v2` basename，刷新嵌套路由时由 FastAPI SPA fallback 返回入口页面。

### 1.6 部署与验证

- Dockerfile 增加前端构建阶段，运行镜像继续只启动一个 FastAPI/Uvicorn 进程。
- README、环境变量模板、Docker Compose 和 v2 API 契约已同步更新。
- 合并后验证结果：
  - 前端：20 个测试文件、66 个测试通过；
  - 前端 lint：通过，保留 2 条既有 Fast Refresh warning；
  - 前端生产构建：通过；
  - 后端：337 个测试通过，1 个跳过。
- 已知限制：Docker 基础镜像下载未在本次会话内完成，因此没有宣称 Docker 镜像构建和容器
  运行冒烟测试通过。

## 2. 学习与可沉淀经验

### 2.1 SSE 适合做通知，不适合替代事实存储

SSE 的价值是降低等待状态的可见延迟。事件只表达“哪个资源变化了”，客户端再读取 REST；
这样不会出现 SSE payload、页面缓存和数据库三套状态需要合并的问题。事件 broker 是进程内实现，
因此必须保持单 FastAPI/Uvicorn 进程；未来若需要多实例部署，应先引入可跨进程分发的消息基础设施。

### 2.2 实时通道必须有收敛机制

浏览器、反向代理和网络都可能让长连接无提示地失效。共享 SSE 加渐进轮询比单独依赖任一方案更稳：
SSE 提供及时性，轮询提供最终一致性。轮询应只覆盖会变化的状态，并在终态停止，避免固定频率刷新
所有页面造成无意义负载。

### 2.3 删除能力要从领域约束开始设计

Episode 删除不是简单增加一个按钮。后端先限制只有终态记录可以删除，并在事务、媒体文件清理、
事件发布和错误契约之间保持一致；前端再依据服务端能力显示入口。数据库删除成功而文件清理失败时，
应记录安全日志并由启动时 orphan 清理兜底，而不是恢复已经提交的数据库记录。

### 2.4 前端类型应以 API 契约为边界

集中维护 wire types、错误解析和 endpoint 函数，可以让页面 hooks 只处理用例状态。新增或修改公开
字段时，应同时更新 API 契约、后端 schema/契约测试和前端类型测试，避免在各页面散落字段转换。

### 2.5 生产托管与开发代理应保持相同 URL 语义

前端始终请求相对 `/api/v2/*`，生产由 FastAPI 同源提供 `/v2/`，开发由 Vite 将 `/api` 代理至
`8000`。两种环境的浏览器请求路径一致，可以减少 CORS、凭据和部署差异。路由 basename、Vite base
和后端 SPA fallback 必须共同测试。

### 2.6 密钥与数据必须作为同一恢复单元

`VIDA_PROFILE_MASTER_KEY` 用于解密 Provider Profile 凭据。它必须和完整的 `data/v2/` 一起备份，
不能在已有数据后随意更换。代码回滚不会自动回滚 SQLite migration、媒体文件或外部 Provider 状态；
涉及数据恢复时必须先停止服务并使用成对备份。

## 3. 回滚操作

### 3.1 回滚前准备

1. 停止 Vite 和 FastAPI 服务，避免回滚过程中继续写入 `data/v2/`。
2. 备份完整 `data/v2/` 目录和当前 `VIDA_PROFILE_MASTER_KEY`，并限制备份文件权限。
3. 确认工作区干净：

   ```bash
   git status --short
   ```

4. 记录当前版本：

   ```bash
   git rev-parse HEAD
   ```

### 3.2 整体回滚本次集成

本次功能提交是连续范围 `4e77170^..928c460`。在已经共享或推送的分支上使用反向提交，不重写历史：

```bash
git switch main
git pull --ff-only
git revert --no-commit 4e77170^..928c460
git commit -m "revert: remove VIDA v2 frontend integration"
```

随后运行后端测试，并检查旧版 `/` 和既有 `/api/*` 行为。该范围也会回滚 SSE、终态 Episode 删除、
章节更新和前端生产托管等配套后端能力。

### 3.3 只回滚前端页面接入

不建议随意挑选中间提交回滚，因为后续页面依赖前面的 API client、类型和事件层。如果需要保留后端
新增能力但撤下 v2 前端，优先按逆序回滚前端及托管相关提交，并在每一步解决依赖：

```bash
git revert 928c460 dc0a592 3c6283c b9f1af2 76257a4 159fd14 10c0f67 0952883 66b50e0 4e77170
```

回滚后运行：

```bash
cd frontend
npm ci
npm test
npm run lint
npm run build
```

### 3.4 数据与 migration 回滚

- 不要用 `git revert`、`git checkout` 或手工删除 SQLite 表来回退生产数据。
- 已应用的 migration 不做就地修改。若旧代码无法读取新数据库，先停止服务，再恢复与旧版本匹配的
  `data/v2/` 和 `VIDA_PROFILE_MASTER_KEY` 成对备份。
- 如果没有可用备份，需要另行设计向前兼容的数据 migration；不要直接删除 `data/v2/`。
- 恢复完成后先在隔离副本上启动并检查 Dashboard、Episode、Provider Profile 和媒体文件，再切换
  正式数据目录。

### 3.5 回滚后的验证

```bash
source venv/bin/activate
python -m unittest discover tests
cd frontend
npm test
npm run build
curl --fail http://127.0.0.1:8000/
curl --fail http://127.0.0.1:8000/api/v2/dashboard
```

如果回滚目标是完全移除 v2，则最后一个 `/api/v2/dashboard` 请求应按目标版本的契约返回 404；
如果只回滚前端，v2 API 应继续返回成功。
