# Library 页面重设计开发记录

日期：2026-07-26

## 1. 开发内容

### 1.1 问题定位

- Library 页面（`frontend/src/pages/LibraryPage.tsx`）此前是无样式的裸实现：搜索框和状态筛选
  没有视觉样式，列表项是标题、状态文本和 Delete 按钮直接拼接（界面上出现
  `www.bilibili.comcompletedDelete` 连排），没有缩略图、日期、时长和空状态。

### 1.2 关键实现

- 页面头部：标题 + 当前库内 Episode 计数；右侧为带 `Search` 图标的搜索输入框和状态筛选
  下拉，均使用与 Transcribe 表单一致的 `border-warm bg-page` 输入样式。
- 列表改为响应式卡片网格（`sm:grid-cols-2 xl:grid-cols-3`）：16:9 缩略图（无缩略图时显示
  `Clapperboard` 占位）、两行截断标题、`formatDate · formatSeconds` 元信息、状态 Badge。
- 状态 Badge 语义与 Dashboard 的 RecentProjectsCard 对齐：completed=success、
  failed=destructive、canceled=muted、queued/processing=copper。
- 操作按钮从裸 `Button` 改为小尺寸：queued/processing 显示 `outline` Cancel，终态显示
  低视觉权重的 `ghost` Delete；按钮放在 Link 之外，避免嵌套交互元素。
- 新增空状态：无数据时引导跳转 `/transcribe`；筛选无结果时提示调整筛选。
- `Load more` 居中并使用 `outline` 变体。

### 1.3 影响范围与验证

- 仅改动 `frontend/src/pages/LibraryPage.tsx`，数据层 `useLibrary`、API 类型和路由未变。
- `npm test`：21 个文件 76 个测试全部通过。
- `npm run lint`：0 error；2 个 warning 为 shadcn 模板 `button.tsx`/`badge.tsx` 的既有
  fast-refresh 提示，与本次改动无关。
- `npm run build`：构建成功。
- 已知限制：WebBridge 浏览器扩展未连接，本次未做浏览器截图验证；视觉样式全部复用现有
  design token 与组件模式，建议下次启动 dev server 后人工过目。

## 2. 学习与可沉淀经验

- 新页面应直接复用 RecentProjectsCard / SubmissionForm 已建立的 token 体系
  （`card-glow`、`border-warm`、`bg-card`、`text-muted-warm`、`tnum`），避免引入新视觉语言。
- 卡片内同时存在跳转和操作时，把操作按钮放在 Link 之外，比 `event.preventDefault()` 拦截
  更清晰，也不会产生嵌套可交互元素的可访问性问题。
- `aria-label="Filter projects"` 和 `aria-label="Filter status"` 保留在原输入元素上，既有
  测试与可访问性语义不变。

## 3. 回滚操作

- 纯前端单文件改动，无数据库、配置或 API 变更，不需要备份。
- 回滚命令：`git checkout -- frontend/src/pages/LibraryPage.tsx`（未提交时）或
  `git revert <commit>`（已提交时）。
- 回滚后验证：`cd frontend && npm test && npm run lint && npm run build`，并打开
  `http://127.0.0.1:7100/v2/library` 确认恢复旧版裸列表。
