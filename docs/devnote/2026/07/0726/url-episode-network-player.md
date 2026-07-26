# URL Episode 网络视频播放器开发记录

日期：2026-07-26

## 1. 开发内容

### 1.1 问题定位

- URL 来源的 Episode 在 Dashboard/Episode 页的播放器播放的是 yt-dlp 下载的 `bestaudio`
  音轨：静态封面 + 音频，看不到原视频。
- 前端 `Episode` 类型没有 `sourceUrl`，无法渲染原平台的网络播放器。

### 1.2 关键实现

- 后端公开契约扩展（已同步 `docs/api/v2-api-contract.md`）：
  `EpisodeSubmissionResponse` / `EpisodeResponse` 新增 `sourceUrl: string | null`，
  按用户提交值原样返回；无数据库结构变更。
- 前端 `src/lib/embed.ts` 新增 `networkEmbedUrl(sourceUrl)`：
  - Bilibili：`/video/BVxxxx` → `https://player.bilibili.com/player.html?bvid=…&autoplay=0`；
  - YouTube：`watch?v=`、`youtu.be/`、`/shorts|embed|live/` → `youtube-nocookie.com/embed/…`；
  - 其他来源（TikTok、SoundCloud、b23.tv 短链、非视频页、直连媒体 URL）返回 `null`。
- `PlayerCard`：`sourceType === "url"` 且可嵌入时渲染 16:9 iframe 网络播放器
  （`allow="autoplay; fullscreen; encrypted-media; picture-in-picture"` + `allowFullScreen`）；
  否则回退到原来的本地 `<video>`（上传来源和不可嵌入来源行为不变）。播放器原本就没有
  章节/文稿 seek 联动，替换 iframe 无功能损失。
- `networkEmbedUrl` 放在 `lib/` 而非组件文件内导出，避免触发
  `react(only-export-components)` fast-refresh 警告。

### 1.3 影响范围与验证

- 新增/更新测试：
  - `test_episode_submission_api.py`：提交与详情响应都包含 `sourceUrl`。
  - `player-card.test.tsx`：Bilibili/YouTube 嵌入 iframe、不可嵌入来源回退 `<video>`、
    非法 URL 与不支持 host 的 `networkEmbedUrl` 单测。
- 验证命令与结果：
  - 后端 `python -m unittest discover tests`：369 通过、1 跳过。
  - 前端 `npm test`：21 文件 80 通过；`npm run lint` 0 error（2 个 shadcn 模板既有
    warning）；`npm run build` 成功。
  - 端到端：重启后端后重新提交 Bilibili 链接，创建响应即含完整 `sourceUrl`。
- 已知限制：
  - b23.tv 短链需要一次重定向解析才能拿到 BV 号，前端不做网络请求，暂不嵌入（回退本地
    播放器）；TikTok/SoundCloud 官方嵌入与播放策略差异大，本次不支持。
  - iframe 内是平台官方播放器，进度与本地转录章节不联动。
  - WebBridge 浏览器扩展未连接，未做真实浏览器截图验证。

## 2. 学习与可沉淀经验

- 嵌入平台官方 iframe 播放器比“下载视频流”或“代理解析直连地址”简单得多：不改采集
  管线、不增加存储和带宽，也没有直连地址过期问题；代价是失去播放进度控制。
- 替换播放器前先确认没有 seek 联动（本例中章节/文稿点击不控制播放器），避免隐性
  功能回退。
- 给响应模型加字段时，Pydantic 响应模型 + OpenAPI 自动同步，人工需要维护的只有契约
  文档中的 TS interface 和 fixtures。

## 3. 回滚操作

- 无数据库结构变更；`sourceUrl` 是响应层新增字段，回滚代码即消失，不需要数据修复。
- 回滚命令：`git revert <commit>`，涉及 `backend/v2/schemas.py`、
  `backend/v2/api/episodes.py`、`frontend/src/lib/embed.ts`、
  `frontend/src/components/dashboard/PlayerCard.tsx`、`frontend/src/api/types.ts`、
  相关测试与 `docs/api/v2-api-contract.md`。
- 回滚后验证：`python -m unittest discover tests`、`cd frontend && npm test && npm run build`，
  并打开任一 URL Episode 确认播放器恢复为本地媒体 `<video>`。
