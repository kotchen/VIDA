# Episode 页时间跳转（Seek Navigation）

## 开发内容

**目标**：在 Episode 详情页点击 Chapter 或 Transcript 中的条目时，播放器跳转到对应时间点。

**关键实现**：

- `frontend/src/lib/embed.ts`：`networkEmbedUrl()` 重构为 `networkEmbed()`，返回
  `{ provider: "bilibili" | "youtube", url }`。url 为不含时间参数的基础 embed URL；
  YouTube 基础 URL 追加 `enablejsapi=1` 以启用 IFrame Player API 的 postMessage 命令通道。
- `frontend/src/components/dashboard/PlayerCard.tsx`：新增可选 prop
  `seek?: { sec: number; nonce: number } | null`。`useEffect` 监听 seek，按三种播放形态分派：
  1. 本地 `<video>`（上传 Episode、不可嵌入 URL）：`videoRef.currentTime = sec` 并尝试 `play()`
     （try/catch 兼容 jsdom 未实现 play 的环境）；
  2. YouTube iframe：`contentWindow.postMessage` 依次发送 `seekTo [sec, true]` 与 `playVideo`
     命令，不刷新 iframe；
  3. Bilibili iframe：无公开跨域 seek API，改为将 iframe src 重写为
     `player.html?bvid=…&t=<sec>&autoplay=1`，通过重载实现从指定时间起播。
  `nonce` 计数保证重复点击同一秒数也会重新触发 effect。
- `TranscriptCard` / `ChapterList`（`ChaptersCard.tsx`）/ `InsightsCard`：新增可选
  `onSeek(sec)` / `onSeekChapter(sec)` 回调。传入时 Transcript 的时间戳、Chapter 的开始时间与
  标题渲染为可点按钮（hover 变金色 + 下划线），未传入时保持原静态样式，Dashboard 页不受影响。
- `EpisodePage.tsx`：新增 `seek` state 与 `seekTo(sec)`（nonce 自增），分别透传给 PlayerCard、
  InsightsCard、TranscriptCard。

**影响范围**：仅前端 Episode 详情页交互；`networkEmbed` 的返回类型变化只影响 PlayerCard 及其
测试两个使用方。无 API、数据布局、配置变更。

**验证结果**：

- `npm test`：22 个文件 88 个测试全部通过（新增 5 个：video currentTime、YouTube postMessage
  参数与 src 不变、Bilibili src 重载带 `&t=`、Transcript 点击回调、Chapter 点击回调）。
- `npm run lint`：0 error，2 个 shadcn 模板既有 warning（与本次改动无关）。
- `npm run build`：成功。

**已知限制**：

- Bilibili 的 `t` 起始时间参数为社区已知行为，未经真实浏览器逐一验证；若平台调整参数名，
  seek 会退化为从头播放。
- YouTube 的 postMessage seek 依赖 IFrame API，首次点击若播放器尚未初始化完成可能丢弃该次
  命令，再次点击即可。
- 时间戳点击跳转未做浏览器端截图验证（WebBridge 未连接），行为由单元测试覆盖。

## 学习与可沉淀经验

- **跨域 iframe 不可直接控制**：嵌入第三方播放器时，seek 能力取决于平台是否提供 API。
  设计时应先按「本地 video / 有 postMessage API / 仅支持 URL 参数」三档分类，再统一成
  一个 `seek` prop 入口，让上层（EpisodePage）不关心差异。
- **用 nonce 而不是布尔值做触发器**：`{ sec, nonce }` 结构让「重复跳到同一时间点」也能
  触发 effect，避免 React 状态值不变导致 effect 不执行的坑；这是事件型 props 的通用模式。
- **重构返回值时保持单一职责**：embed URL 生成器只产出「基础 URL + provider」，
  seek 参数的拼接留在 PlayerCard，避免把播放器状态逻辑泄漏进纯函数。
- jsdom 的 `HTMLMediaElement.play()` 未实现但不抛异常（返回 undefined 并输出 stderr 噪音），
  调用处用 `try/catch + 可选链 catch` 双保险即可，无需在测试中 mock。

## 回滚操作

1. 停止前端 dev server（Ctrl+C）；本改动不涉后端，无需重启 FastAPI。
2. 无数据迁移、无配置变更、无备份要求。
3. Git 回滚（确认工作区无其他未保存改动后）：

   ```bash
   git checkout -- frontend/src/lib/embed.ts \
     frontend/src/components/dashboard/PlayerCard.tsx \
     frontend/src/components/dashboard/TranscriptCard.tsx \
     frontend/src/components/dashboard/ChaptersCard.tsx \
     frontend/src/components/dashboard/InsightsCard.tsx \
     frontend/src/pages/EpisodePage.tsx \
     frontend/src/test/player-card.test.tsx \
     frontend/src/test/transcript-card.test.tsx \
     frontend/src/test/insights-card.test.tsx
   ```

4. 回滚后验证：`cd frontend && npm test && npm run lint && npm run build` 全部通过；
   打开任一已完成 Episode，确认 Transcript/Chapter 时间戳恢复为纯文本不可点击。
