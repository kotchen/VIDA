# VIDA 2.0 Dashboard UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已批准的设计稿，在 `frontend/` 目录用 React + TypeScript + Tailwind + shadcn/ui 高保真实现 VIDA 2.0 Dashboard 主视图（Mock 数据）。

**Architecture:** 设计系统先行：先落设计令牌（`theme/tokens.css`）与数据契约（`data/types.ts` + `MockProvider`），再实现 AppShell（路由 + 侧栏 + 顶栏）与 7 个独立面板组件，最后在 DashboardPage 组装。现有 `backend/` 与 `static/` 完全不动。

**Tech Stack:** Vite 5+ / React 18+ / TypeScript / Tailwind CSS v4（`@tailwindcss/vite`）/ shadcn/ui（new-york）/ react-router v7 / lucide-react / Vitest + Testing Library / @fontsource 字体

**Spec:** `docs/superpowers/specs/2026-07-18-vida-2-dashboard-ui-design.md`

## Global Constraints

- 工作根目录：`D:\aaron\VIDA`；**不得改动** `backend/`、`static/`、`docs/` 以外的既有文件；本计划只新增 `frontend/` 目录。
- Dev 服务器端口固定 **7100**（`vite.config.ts` 中 `server: { port: 7100, host: true }`）。
- UI 文案全部为**英文**（与设计图一致）。
- 色板/字体必须来自 Task 2 的令牌，组件中禁止出现未令牌化的硬编码色值（少数一次性渐变端点除外，已在令牌中定义为工具类）。
- 时长一律秒（`*Sec`），时间一律 ISO 8601 字符串；数据模型以 `data/types.ts` 为准。
- 桌面优先：≥1280px 正常渲染，<1280px 显示英文兜底提示。
- 无用户系统：禁止出现问候语、头像、通知铃铛、Pro Plan 卡片。
- Tailwind 使用 **v4**（CSS 优先配置，无 `tailwind.config.ts`）——这是对设计文档第 2 节文件清单的有意偏离：令牌同样集中在 `src/theme/tokens.css`，职责不变。
- shadcn/ui 只安装本阶段实际用到的 4 个组件：`button`、`card`、`badge`、`scroll-area`（YAGNI）。
- 每个 Task 完成后按步骤提交 git；commit message 使用约定式提交。

---

### Task 1: 项目脚手架（Vite + React + TS + Tailwind v4 + Vitest）

**Files:**
- Create: `frontend/`（整个目录，由脚手架生成后改造）
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/test/smoke.test.ts`
- Create: `frontend/src/assets/*.png`（10 张占位图）
- Modify: `frontend/tsconfig.app.json`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Produces: 可运行的 Vite 工程（dev 端口 7100）；`@` → `src` 路径别名；Vitest 可用（globals + jsdom + jest-dom）；`src/assets/` 下 10 张占位 PNG（后续 Task 14 用 AI 图覆盖，文件名即契约）：
  `splash-sidebar.png` `poster-episode.png` `chapter-1.png` `chapter-2.png` `chapter-3.png` `chapter-4.png` `project-1.png` `project-2.png` `project-3.png` `project-4.png`

- [ ] **Step 1: 生成 Vite 脚手架**

```bash
cd /d/aaron/VIDA
npm create vite@latest frontend -- --template react-ts
cd frontend
```

- [ ] **Step 2: 安装依赖**

```bash
cd /d/aaron/VIDA/frontend
npm install react-router lucide-react clsx tailwind-merge class-variance-authority \
  tailwindcss @tailwindcss/vite \
  @fontsource/dm-serif-display @fontsource/manrope
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom
```

- [ ] **Step 3: 写 `vite.config.ts`（端口 7100 + 别名 + Vitest）**

```ts
/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { port: 7100, host: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
})
```

- [ ] **Step 4: 配置 TS：`tsconfig.app.json` 的 `compilerOptions` 中加入别名与测试类型**

```json
"baseUrl": ".",
"paths": { "@/*": ["./src/*"] },
"types": ["vite/client", "vitest/globals", "@testing-library/jest-dom"]
```

- [ ] **Step 5: 写 Vitest setup 与冒烟测试**

`src/test/setup.ts`：
```ts
import "@testing-library/jest-dom/vitest"
```

`src/test/smoke.test.ts`：
```ts
describe("scaffold", () => {
  it("vitest works", () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 6: 生成 10 张占位素材图（深棕色块，文件名即契约）**

```bash
cd /d/aaron/VIDA
python -c "
from PIL import Image
import os
os.makedirs('frontend/src/assets', exist_ok=True)
names = ['splash-sidebar','poster-episode','chapter-1','chapter-2','chapter-3','chapter-4','project-1','project-2','project-3','project-4']
for n in names:
    Image.new('RGB', (640, 360), (42, 22, 9)).save('frontend/src/assets/' + n + '.png')
print('ok', len(names))
"
```
预期输出：`ok 10`

- [ ] **Step 7: 精简脚手架自带文件**

删除：`src/App.css`、`src/assets/react.svg`、`public/vite.svg`、`src/index.css`。

把 `src/App.tsx` 整个替换为临时占位版（Task 5 重写为路由版）：

```tsx
export default function App() {
  return <div>VIDA 2.0</div>
}
```

把 `src/main.tsx` 改为（`./theme/tokens.css` 在 Task 2 创建，此处先不引入）：

```tsx
import React from "react"
import ReactDOM from "react-dom/client"
import App from "./App"
import "@fontsource/dm-serif-display/400.css"
import "@fontsource/manrope/400.css"
import "@fontsource/manrope/500.css"
import "@fontsource/manrope/600.css"
import "@fontsource/manrope/700.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

并把 `index.html` 中 `<title>` 改为 `<title>VIDA — Video Intelligence, Dialogue, Analysis</title>`，删除对 `/vite.svg` 的 favicon 引用行。

- [ ] **Step 8: 验证**

```bash
cd /d/aaron/VIDA/frontend
npx vitest run
npm run build
```
预期：`1 passed`；build 成功零错误。

- [ ] **Step 9: Commit**

```bash
cd /d/aaron/VIDA
git add frontend
git commit -m "feat(frontend): scaffold VIDA 2.0 vite+react+ts+tailwind4 app with vitest"
```

---

### Task 2: 设计令牌（tokens.css）

**Files:**
- Create: `frontend/src/theme/tokens.css`
- Test: `frontend/src/test/tokens.test.ts`
- Modify: `frontend/src/main.tsx`（引入 tokens.css）

**Interfaces:**
- Produces（后续所有任务依赖的 Tailwind 类/工具类）：
  - 颜色工具类：`bg-page` `bg-sidebar` `bg-card` `bg-raised` `border-warm` `text-cream` `text-muted-warm` `text-gold` `text-copper-300` `text-copper-500` `bg-copper-500` `border-copper-500` `text-success` `bg-success` `border-success`
  - 复合工具类：`.bg-copper-gradient`（主按钮/徽标铜金渐变）、`.text-gold-gradient`（Logo/标题金字）、`.card-glow`（卡片描边+内发光）、`.tnum`（等宽数字）
  - 字体：`font-display`（DM Serif Display）、`font-sans`（Manrope，全局默认）
  - shadcn 语义变量：`:root` 下的 `--background` 等 + `@theme inline` 映射

- [ ] **Step 1: 写失败测试 `src/test/tokens.test.ts`**

```ts
import { readFileSync } from "node:fs"

const css = readFileSync(new URL("../theme/tokens.css", import.meta.url), "utf-8")

describe("design tokens", () => {
  it.each([
    "--color-page: #120B05",
    "--color-sidebar: #110802",
    "--color-card: #1F140A",
    "--color-raised: #2A1609",
    "--color-warm: #46301E",
    "--color-copper-300: #D09050",
    "--color-copper-500: #B07030",
    "--color-copper-700: #905020",
    "--color-gold: #C28D51",
    "--color-cream: #F3E9DA",
    "--color-muted-warm: #A68B70",
    "--color-success: #7BA05B",
  ])("defines %s", (token) => {
    expect(css).toContain(token)
  })

  it("defines composite utilities", () => {
    for (const cls of [".bg-copper-gradient", ".text-gold-gradient", ".card-glow", ".tnum"]) {
      expect(css).toContain(cls)
    }
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/tokens.test.ts
```
预期：FAIL（`tokens.css` 不存在，readFileSync 抛错）。

- [ ] **Step 3: 写 `src/theme/tokens.css`**

```css
@import "tailwindcss";

@theme {
  --color-page: #120B05;
  --color-sidebar: #110802;
  --color-raised: #2A1609;
  --color-warm: #46301E;
  --color-copper-300: #D09050;
  --color-copper-500: #B07030;
  --color-copper-700: #905020;
  --color-gold: #C28D51;
  --color-cream: #F3E9DA;
  --color-muted-warm: #A68B70;
  --color-success: #7BA05B;

  --font-display: "DM Serif Display", Georgia, serif;
  --font-sans: "Manrope", ui-sans-serif, system-ui, sans-serif;
}

/* shadcn/ui 语义变量（new-york, cssVariables 模式） */
:root {
  --background: #120B05;
  --foreground: #F3E9DA;
  --card: #1F140A;
  --card-foreground: #F3E9DA;
  --popover: #2A1609;
  --popover-foreground: #F3E9DA;
  --primary: #B07030;
  --primary-foreground: #120B05;
  --secondary: #2A1609;
  --secondary-foreground: #F3E9DA;
  --muted: #2A1609;
  --muted-foreground: #A68B70;
  --accent: #2A1609;
  --accent-foreground: #F3E9DA;
  --destructive: #A04030;
  --destructive-foreground: #F3E9DA;
  --border: #46301E;
  --input: #46301E;
  --ring: #B07030;
  --radius: 1rem;
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 4px);
}

body {
  background-color: var(--color-page);
  color: var(--color-cream);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
}

/* 铜金渐变（主按钮 / 徽标 / 导航激活态） */
.bg-copper-gradient {
  background-image: linear-gradient(135deg, #905020 0%, #D09050 100%);
}

/* 金色渐变文字（Logo / 面板标题） */
.text-gold-gradient {
  background-image: linear-gradient(135deg, #E8C284 0%, #C28D51 45%, #905020 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

/* 卡片：暖棕细描边 + 极轻内发光 */
.card-glow {
  box-shadow:
    inset 0 1px 0 0 rgba(208, 144, 80, 0.08),
    0 0 0 1px rgba(70, 48, 30, 0.6);
}

/* 等宽数字（时间戳 / 时长） */
.tnum {
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 4: `main.tsx` 顶部（App import 之后）引入令牌**

在 `src/main.tsx` 的 `import App from "./App"` 之后、字体 import 之后加一行：

```tsx
import "./theme/tokens.css"
```

- [ ] **Step 5: 运行测试确认通过 + 构建通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run && npm run build
```
预期：全部 PASS；build 零错误。

- [ ] **Step 6: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/theme frontend/src/test frontend/src/main.tsx
git commit -m "feat(frontend): add VIDA design tokens (copper/soda dark palette)"
```

---

### Task 3: shadcn/ui 初始化与基础组件

**Files:**
- Create: `frontend/components.json`
- Create: `frontend/src/lib/utils.ts`
- Create: `frontend/src/components/ui/{button,card,badge,scroll-area}.tsx`（CLI 生成）

**Interfaces:**
- Produces: `@/components/ui/button` 导出 `Button`；`@/components/ui/card` 导出 `Card`；`@/components/ui/badge` 导出 `Badge`；`@/components/ui/scroll-area` 导出 `ScrollArea`；`@/lib/utils` 导出 `cn()`。

- [ ] **Step 1: 写 `components.json`**

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/theme/tokens.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 2: 写 `src/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 3: 用 CLI 生成 4 个组件**

```bash
cd /d/aaron/VIDA/frontend
npx shadcn@latest add button card badge scroll-area -y -o
```

- [ ] **Step 4: 验证构建**

```bash
cd /d/aaron/VIDA/frontend && npm run build && npx vitest run
```
预期：build 零错误；测试全绿。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/components.json frontend/src/lib frontend/src/components
git commit -m "feat(frontend): init shadcn/ui (button, card, badge, scroll-area)"
```

---

### Task 4: 数据层（types + format + mock + provider）

**Files:**
- Create: `frontend/src/data/types.ts`
- Create: `frontend/src/lib/format.ts`
- Create: `frontend/src/data/mock.ts`
- Create: `frontend/src/data/provider.ts`
- Test: `frontend/src/test/format.test.ts`
- Test: `frontend/src/test/provider.test.ts`

**Interfaces:**
- Consumes: Task 1 的占位素材图（`src/assets/*.png`）。
- Produces:
  - `formatSeconds(sec: number): string` → `"57:42"`；`formatTimestamp(sec: number): string` → `"00:00:09"`；`formatDate(iso: string): string` → `"May 16, 2024"`（UTC，时区无关）
  - `types.ts` 导出 `Episode` `TranscriptSegment` `Chapter` `Summary` `Project` `DashboardData`（字段与设计文档第 5 节完全一致）
  - `provider.ts` 导出接口 `DataProvider { getDashboard(): Promise<DashboardData> }`、实现 `MockProvider`、单例 `dataProvider`
  - `mock.ts` 导出 `mockDashboard: DashboardData`（设计图同款数据）

- [ ] **Step 1: 写失败测试 `src/test/format.test.ts`**

```ts
import { formatDate, formatSeconds, formatTimestamp } from "../lib/format"

describe("formatSeconds", () => {
  it("formats mm:ss", () => {
    expect(formatSeconds(3462)).toBe("57:42")
    expect(formatSeconds(105)).toBe("1:45")
    expect(formatSeconds(9)).toBe("0:09")
  })
  it("formats h:mm:ss when >= 1 hour", () => {
    expect(formatSeconds(3723)).toBe("1:02:03")
  })
})

describe("formatTimestamp", () => {
  it("formats hh:mm:ss padded", () => {
    expect(formatTimestamp(0)).toBe("00:00:00")
    expect(formatTimestamp(9)).toBe("00:00:09")
    expect(formatTimestamp(62)).toBe("00:01:02")
  })
})

describe("formatDate", () => {
  it("formats en-US short date in UTC", () => {
    expect(formatDate("2024-05-16T09:00:00Z")).toBe("May 16, 2024")
  })
})
```

- [ ] **Step 2: 写失败测试 `src/test/provider.test.ts`**

```ts
import { dataProvider } from "../data/provider"

describe("MockProvider", () => {
  it("returns dashboard data matching the design mock", async () => {
    const data = await dataProvider.getDashboard()
    expect(data.currentEpisode.title).toBe("AI Podcast Episode 12")
    expect(data.currentEpisode.status).toBe("completed")
    expect(data.summary.confidence).toBe(92)
    expect(data.transcript).toHaveLength(6)
    expect(data.chapters).toHaveLength(6)
    expect(data.recentProjects).toHaveLength(4)
    expect(data.recentProjects[3].status).toBe("processing")
  })

  it("every transcript segment has speaker and ordered timestamps", async () => {
    const { transcript } = await dataProvider.getDashboard()
    for (const seg of transcript) {
      expect(seg.speaker.length).toBeGreaterThan(0)
      expect(seg.endSec).toBeGreaterThan(seg.startSec)
      expect(seg.text.length).toBeGreaterThan(0)
    }
  })
})
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/format.test.ts src/test/provider.test.ts
```
预期：FAIL（模块不存在）。

- [ ] **Step 4: 写 `src/lib/format.ts`**

```ts
export function formatSeconds(total: number): string {
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = Math.floor(total % 60)
  const ss = String(s).padStart(2, "0")
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${ss}`
  return `${m}:${ss}`
}

export function formatTimestamp(total: number): string {
  const h = String(Math.floor(total / 3600)).padStart(2, "0")
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0")
  const s = String(Math.floor(total % 60)).padStart(2, "0")
  return `${h}:${m}:${s}`
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  })
}
```

- [ ] **Step 5: 写 `src/data/types.ts`**

```ts
export interface Episode {
  id: string
  title: string
  sourceType: "upload" | "url"
  mediaUrl: string
  posterUrl: string
  durationSec: number
  resolution?: string
  status: "completed" | "processing" | "failed"
  language: string
  createdAt: string
}

export interface TranscriptSegment {
  id: string
  startSec: number
  endSec: number
  speaker: string
  text: string
}

export interface Chapter {
  id: string
  startSec: number
  title: string
  durationSec: number
  thumbnailUrl: string
  bookmarked: boolean
}

export interface Summary {
  episodeId: string
  content: string
  readTimeMin: number
  keyPoints: number
  confidence: number
  generatedBy: string
}

export interface Project {
  id: string
  title: string
  createdAt: string
  durationSec: number
  status: Episode["status"]
  thumbnailUrl: string
}

export interface DashboardData {
  currentEpisode: Episode
  summary: Summary
  transcript: TranscriptSegment[]
  chapters: Chapter[]
  recentProjects: Project[]
}
```

- [ ] **Step 6: 写 `src/data/mock.ts`**

```ts
import chapter1 from "../assets/chapter-1.png"
import chapter2 from "../assets/chapter-2.png"
import chapter3 from "../assets/chapter-3.png"
import chapter4 from "../assets/chapter-4.png"
import posterEpisode from "../assets/poster-episode.png"
import project1 from "../assets/project-1.png"
import project2 from "../assets/project-2.png"
import project3 from "../assets/project-3.png"
import project4 from "../assets/project-4.png"
import type { DashboardData } from "./types"

export const mockDashboard: DashboardData = {
  currentEpisode: {
    id: "ep-12",
    title: "AI Podcast Episode 12",
    sourceType: "upload",
    mediaUrl: "",
    posterUrl: posterEpisode,
    durationSec: 3462,
    resolution: "1080p",
    status: "completed",
    language: "en",
    createdAt: "2024-05-16T09:00:00Z",
  },
  summary: {
    episodeId: "ep-12",
    content:
      "In this episode, the hosts explore the latest advancements in AI, focusing on practical applications, ethical considerations, and the future of human-AI collaboration. They discuss real-world use cases, debunk common myths, and share predictions for the next 5 years.",
    readTimeMin: 6,
    keyPoints: 8,
    confidence: 92,
    generatedBy: "VIDA",
  },
  transcript: [
    { id: "seg-1", startSec: 0, endSec: 9, speaker: "Alex", text: "Welcome back to the AI Podcast! I'm Alex, and I'm joined by my co-host, Sam." },
    { id: "seg-2", startSec: 9, endSec: 20, speaker: "Sam", text: "Hey everyone! Today we're diving into the real-world applications of AI that are making the biggest impact in 2024." },
    { id: "seg-3", startSec: 20, endSec: 34, speaker: "Alex", text: "Absolutely. From healthcare to creative tools, AI is everywhere. Let's start with healthcare—what's exciting you most?" },
    { id: "seg-4", startSec: 34, endSec: 48, speaker: "Sam", text: "The breakthroughs in protein folding predictions. Tools like AlphaFold have accelerated drug discovery by years." },
    { id: "seg-5", startSec: 48, endSec: 62, speaker: "Alex", text: "And on the creative side, generative AI is empowering creators like never before. But there are ethical questions we can't ignore." },
    { id: "seg-6", startSec: 62, endSec: 75, speaker: "Sam", text: "Exactly. Bias, transparency, and responsible deployment are critical as these systems scale." },
  ],
  chapters: [
    { id: "ch-1", startSec: 0, title: "Introduction & Welcome", durationSec: 105, thumbnailUrl: chapter1, bookmarked: false },
    { id: "ch-2", startSec: 105, title: "AI in Healthcare", durationSec: 552, thumbnailUrl: chapter2, bookmarked: false },
    { id: "ch-3", startSec: 717, title: "Creative AI Revolution", durationSec: 548, thumbnailUrl: chapter3, bookmarked: false },
    { id: "ch-4", startSec: 1265, title: "Ethical Considerations", durationSec: 456, thumbnailUrl: chapter4, bookmarked: false },
    { id: "ch-5", startSec: 1721, title: "The Future of AI", durationSec: 382, thumbnailUrl: chapter1, bookmarked: false },
    { id: "ch-6", startSec: 2060, title: "Q&A and Closing Thoughts", durationSec: 1402, thumbnailUrl: chapter2, bookmarked: false },
  ],
  recentProjects: [
    { id: "ep-12", title: "AI Podcast Episode 12", createdAt: "2024-05-16T09:00:00Z", durationSec: 3462, status: "completed", thumbnailUrl: project1 },
    { id: "ep-11", title: "Product Launch Talk", createdAt: "2024-05-14T09:00:00Z", durationSec: 2058, status: "completed", thumbnailUrl: project2 },
    { id: "ep-10", title: "Customer Interview #7", createdAt: "2024-05-10T09:00:00Z", durationSec: 1351, status: "completed", thumbnailUrl: project3 },
    { id: "ep-9", title: "Design Sprint Debrief", createdAt: "2024-05-08T09:00:00Z", durationSec: 2469, status: "processing", thumbnailUrl: project4 },
  ],
}
```

- [ ] **Step 7: 写 `src/data/provider.ts`**

```ts
import { mockDashboard } from "./mock"
import type { DashboardData } from "./types"

export interface DataProvider {
  getDashboard(): Promise<DashboardData>
}

export class MockProvider implements DataProvider {
  async getDashboard(): Promise<DashboardData> {
    return mockDashboard
  }
}

export const dataProvider: DataProvider = new MockProvider()
```

- [ ] **Step 8: 运行测试确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run
```
预期：全部 PASS（smoke + tokens + format + provider）。

- [ ] **Step 9: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/data frontend/src/lib frontend/src/test
git commit -m "feat(frontend): add dashboard data layer (types, mock, provider, formatters)"
```

---

### Task 5: App 骨架（路由 + AppShell + Sidebar + TopBar + PlaceholderPage + 窄屏兜底）

**Files:**
- Create: `frontend/src/components/layout/AppShell.tsx`
- Create: `frontend/src/components/layout/Sidebar.tsx`
- Create: `frontend/src/components/layout/TopBar.tsx`
- Create: `frontend/src/pages/PlaceholderPage.tsx`
- Create: `frontend/src/pages/DashboardPage.tsx`（临时简版，Task 13 重写）
- Modify: `frontend/src/App.tsx`（整个重写）
- Modify: `frontend/src/main.tsx`（挂 BrowserRouter）
- Test: `frontend/src/test/sidebar.test.tsx`

**Interfaces:**
- Consumes: `src/assets/splash-sidebar.png`（Task 1 占位图）。
- Produces:
  - 路由：`/` → Navigate 到 `/dashboard`；`/dashboard` → DashboardPage；`/transcribe` `/library` `/summaries` `/settings` → `PlaceholderPage`
  - `Sidebar`（导航项常量 `NAV`，5 项）、`TopBar`（居中搜索框）、`AppShell`（<1280px 显示兜底文案 "VIDA 2.0 is best experienced on desktop."）

- [ ] **Step 1: 写失败测试 `src/test/sidebar.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { Sidebar } from "../components/layout/Sidebar"

describe("Sidebar", () => {
  it("renders brand and all five nav items", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Sidebar />
      </MemoryRouter>
    )
    expect(screen.getByText("VIDA")).toBeInTheDocument()
    expect(screen.getByText("Video Intelligence, Dialogue, Analysis")).toBeInTheDocument()
    for (const label of ["Dashboard", "Transcribe", "Library", "Summaries", "Settings"]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText(/Fueling insights/)).toBeInTheDocument()
  })

  it("marks the active route", () => {
    render(
      <MemoryRouter initialEntries={["/library"]}>
        <Sidebar />
      </MemoryRouter>
    )
    const library = screen.getByText("Library").closest("a")
    expect(library?.className).toContain("bg-copper-gradient")
    const dashboard = screen.getByText("Dashboard").closest("a")
    expect(dashboard?.className).not.toContain("bg-copper-gradient")
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/sidebar.test.tsx
```
预期：FAIL（Sidebar 不存在）。

- [ ] **Step 3: 写 `src/components/layout/Sidebar.tsx`**

```tsx
import { FileText, LayoutDashboard, Library, Mic, Settings } from "lucide-react"
import { NavLink } from "react-router"
import splashUrl from "../../assets/splash-sidebar.png"

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/transcribe", label: "Transcribe", icon: Mic },
  { to: "/library", label: "Library", icon: Library },
  { to: "/summaries", label: "Summaries", icon: FileText },
  { to: "/settings", label: "Settings", icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="relative flex h-screen w-[280px] shrink-0 flex-col overflow-hidden border-r border-warm/60 bg-sidebar">
      <div className="px-6 pb-8 pt-7">
        <div className="font-display text-4xl tracking-wide">
          <span className="text-gold-gradient">VIDA</span>
        </div>
        <p className="mt-1 text-xs text-muted-warm">Video Intelligence, Dialogue, Analysis</p>
      </div>
      <nav className="flex flex-col gap-1 px-3">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors ${
                isActive
                  ? "bg-copper-gradient font-semibold text-[#1A0E04]"
                  : "text-muted-warm hover:bg-raised hover:text-cream"
              }`
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto">
        <img src={splashUrl} alt="" className="h-44 w-full object-cover" />
        <p className="px-6 pb-5 pt-3 text-xs leading-relaxed text-muted-warm">
          Fueling insights,
          <br />
          one conversation at a time.
        </p>
      </div>
    </aside>
  )
}
```

- [ ] **Step 4: 写 `src/components/layout/TopBar.tsx`**

```tsx
import { Search } from "lucide-react"

export function TopBar() {
  return (
    <header className="flex h-14 shrink-0 items-center justify-center border-b border-warm/60 px-6">
      <div className="flex w-full max-w-xl items-center gap-2 rounded-xl border border-warm/60 bg-card px-4 py-2 text-muted-warm">
        <Search className="size-4 shrink-0" />
        <input
          className="w-full bg-transparent text-sm text-cream outline-none placeholder:text-muted-warm"
          placeholder="Search projects, transcripts, summaries..."
        />
        <kbd className="shrink-0 rounded-md border border-warm/60 px-1.5 py-0.5 text-[10px]">⌘K</kbd>
      </div>
    </header>
  )
}
```

- [ ] **Step 5: 写 `src/components/layout/AppShell.tsx`（含窄屏兜底）**

```tsx
import { Outlet } from "react-router"
import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"

export function AppShell() {
  return (
    <div className="min-h-screen bg-page">
      <div className="hidden min-[1280px]:flex">
        <Sidebar />
        <div className="flex min-h-screen flex-1 flex-col">
          <TopBar />
          <main className="flex-1">
            <Outlet />
          </main>
        </div>
      </div>
      <div className="flex min-h-screen items-center justify-center p-8 min-[1280px]:hidden">
        <p className="text-center text-sm text-muted-warm">
          VIDA 2.0 is best experienced on desktop.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: 写 `src/pages/PlaceholderPage.tsx` 与临时 `src/pages/DashboardPage.tsx`**

```tsx
import { Sparkles } from "lucide-react"

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col items-center justify-center gap-3">
      <Sparkles className="size-8 text-gold" />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-warm">Coming in VIDA 2.x</p>
    </div>
  )
}
```

```tsx
export function DashboardPage() {
  return <div className="p-6 text-sm text-muted-warm">Dashboard</div>
}
```

- [ ] **Step 7: 重写 `src/App.tsx`，并给 `main.tsx` 挂路由**

`src/App.tsx`：
```tsx
import { Navigate, Route, Routes } from "react-router"
import { AppShell } from "./components/layout/AppShell"
import { DashboardPage } from "./pages/DashboardPage"
import { PlaceholderPage } from "./pages/PlaceholderPage"

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/transcribe" element={<PlaceholderPage title="Transcribe" />} />
        <Route path="/library" element={<PlaceholderPage title="Library" />} />
        <Route path="/summaries" element={<PlaceholderPage title="Summaries" />} />
        <Route path="/settings" element={<PlaceholderPage title="Settings" />} />
      </Route>
    </Routes>
  )
}
```

`src/main.tsx` 中把 `<App />` 包进 BrowserRouter：

```tsx
import React from "react"
import ReactDOM from "react-dom/client"
import { BrowserRouter } from "react-router"
import App from "./App"
import "@fontsource/dm-serif-display/400.css"
import "@fontsource/manrope/400.css"
import "@fontsource/manrope/500.css"
import "@fontsource/manrope/600.css"
import "@fontsource/manrope/700.css"
import "./theme/tokens.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
```

- [ ] **Step 8: 运行测试 + 构建**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run && npm run build
```
预期：全部 PASS；build 零错误。

- [ ] **Step 9: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src
git commit -m "feat(frontend): add app shell with sidebar, topbar, routing and placeholders"
```

---

### Task 6: UploadCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/UploadCard.tsx`
- Test: `frontend/src/test/upload-card.test.tsx`

**Interfaces:**
- Consumes: `@/components/ui/card` 的 `Card`。
- Produces: `UploadCard()`（无 props；纯静态展示，本阶段不实现真实上传）。

- [ ] **Step 1: 写失败测试 `src/test/upload-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { UploadCard } from "../components/dashboard/UploadCard"

describe("UploadCard", () => {
  it("renders upload copy", () => {
    render(<UploadCard />)
    expect(screen.getByText("Upload a video or audio file")).toBeInTheDocument()
    expect(screen.getByText(/Drag & drop a file here/)).toBeInTheDocument()
    expect(screen.getByText(/MP4, MOV, MKV, AVI, MP3, M4A/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/upload-card.test.tsx
```
预期：FAIL（组件不存在）。

- [ ] **Step 3: 写 `src/components/dashboard/UploadCard.tsx`**

```tsx
import { CloudUpload } from "lucide-react"
import { Card } from "@/components/ui/card"

export function UploadCard() {
  return (
    <Card className="card-glow flex h-full flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-copper-500/50 bg-card p-6 text-center">
      <CloudUpload className="size-10 text-copper-300" strokeWidth={1.5} />
      <p className="text-base font-semibold">Upload a video or audio file</p>
      <p className="text-sm text-muted-warm">Drag &amp; drop a file here, or click to browse</p>
      <p className="text-xs text-muted-warm/70">MP4, MOV, MKV, AVI, MP3, M4A · Up to 5GB</p>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/upload-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard frontend/src/test/upload-card.test.tsx
git commit -m "feat(frontend): add UploadCard panel"
```

---

### Task 7: PlayerCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/PlayerCard.tsx`
- Test: `frontend/src/test/player-card.test.tsx`

**Interfaces:**
- Consumes: `Episode`（`@/data/types`）、`formatDate` `formatSeconds`（`@/lib/format`）、`Card` `Badge`。
- Produces: `PlayerCard({ episode }: { episode: Episode })`。

- [ ] **Step 1: 写失败测试 `src/test/player-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { PlayerCard } from "../components/dashboard/PlayerCard"
import { mockDashboard } from "../data/mock"

describe("PlayerCard", () => {
  it("renders episode title and meta", () => {
    render(<PlayerCard episode={mockDashboard.currentEpisode} />)
    expect(screen.getByText("AI Podcast Episode 12")).toBeInTheDocument()
    expect(screen.getByText("May 16, 2024")).toBeInTheDocument()
    expect(screen.getByText("57:42")).toBeInTheDocument()
    expect(screen.getByText("1080p")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
    expect(screen.getByText("00:00 / 57:42")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/player-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/PlayerCard.tsx`**

```tsx
import { Captions, Maximize2, MoreVertical, Pencil, Play } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Episode } from "@/data/types"
import { formatDate, formatSeconds } from "@/lib/format"

export function PlayerCard({ episode }: { episode: Episode }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gold">{episode.title}</h2>
        <div className="flex items-center gap-2 text-muted-warm">
          <Pencil className="size-4" />
          <MoreVertical className="size-4" />
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-warm">
        <span>{formatDate(episode.createdAt)}</span>
        <span className="tnum">{formatSeconds(episode.durationSec)}</span>
        {episode.resolution ? <span>{episode.resolution}</span> : null}
        {episode.status === "completed" ? (
          <Badge className="border-success/40 bg-success/15 text-success">Completed</Badge>
        ) : null}
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl">
        <img src={episode.posterUrl} alt={episode.title} className="absolute inset-0 size-full object-cover" />
        <button
          aria-label="Play"
          className="bg-copper-gradient absolute left-1/2 top-1/2 flex size-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full text-[#1A0E04] shadow-lg"
        >
          <Play className="size-5 fill-current" />
        </button>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-warm">
        <span className="tnum">00:00 / {formatSeconds(episode.durationSec)}</span>
        <div className="h-1 flex-1 rounded-full bg-raised">
          <div className="bg-copper-gradient h-full w-0 rounded-full" />
        </div>
        <span>1x</span>
        <Captions className="size-4" />
        <Maximize2 className="size-4" />
      </div>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/player-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/PlayerCard.tsx frontend/src/test/player-card.test.tsx
git commit -m "feat(frontend): add PlayerCard panel"
```

---

### Task 8: SummaryCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/SummaryCard.tsx`
- Test: `frontend/src/test/summary-card.test.tsx`

**Interfaces:**
- Consumes: `Summary`（`@/data/types`）、`Card` `Badge`。
- Produces: `SummaryCard({ summary }: { summary: Summary })`。

- [ ] **Step 1: 写失败测试 `src/test/summary-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { SummaryCard } from "../components/dashboard/SummaryCard"
import { mockDashboard } from "../data/mock"

describe("SummaryCard", () => {
  it("renders summary content and stats", () => {
    render(<SummaryCard summary={mockDashboard.summary} />)
    expect(screen.getByText(/AI Summary/)).toBeInTheDocument()
    expect(screen.getByText("Generated by VIDA")).toBeInTheDocument()
    expect(screen.getByText(/In this episode, the hosts explore/)).toBeInTheDocument()
    expect(screen.getByText("6 min")).toBeInTheDocument()
    expect(screen.getByText("8")).toBeInTheDocument()
    expect(screen.getByText("92%")).toBeInTheDocument()
    expect(screen.getByText("Read time")).toBeInTheDocument()
    expect(screen.getByText("Key points")).toBeInTheDocument()
    expect(screen.getByText("Confidence")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/summary-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/SummaryCard.tsx`**

```tsx
import { Clock, Gauge, ListChecks, Sparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Summary } from "@/data/types"

export function SummaryCard({ summary }: { summary: Summary }) {
  const stats = [
    { icon: Clock, value: `${summary.readTimeMin} min`, label: "Read time" },
    { icon: ListChecks, value: String(summary.keyPoints), label: "Key points" },
    { icon: Gauge, value: `${summary.confidence}%`, label: "Confidence" },
  ]
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-semibold text-gold">
          AI Summary <Sparkles className="size-4" />
        </h2>
        <Badge variant="outline" className="border-copper-500/50 text-[10px] text-copper-300">
          Generated by {summary.generatedBy}
        </Badge>
      </div>
      <p className="text-sm leading-relaxed text-cream/90">{summary.content}</p>
      <div className="mt-auto grid grid-cols-3 gap-2">
        {stats.map(({ icon: Icon, value, label }) => (
          <div key={label} className="flex flex-col items-center gap-1 rounded-xl bg-raised py-2.5">
            <Icon className="size-4 text-copper-300" />
            <span className="tnum text-sm font-semibold">{value}</span>
            <span className="text-[10px] text-muted-warm">{label}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/summary-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/SummaryCard.tsx frontend/src/test/summary-card.test.tsx
git commit -m "feat(frontend): add SummaryCard panel"
```

---

### Task 9: TranscriptCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/TranscriptCard.tsx`
- Test: `frontend/src/test/transcript-card.test.tsx`

**Interfaces:**
- Consumes: `TranscriptSegment`（`@/data/types`）、`formatTimestamp`（`@/lib/format`）、`Card` `ScrollArea`。
- Produces: `TranscriptCard({ segments }: { segments: TranscriptSegment[] })`；首段（index 0）以 `bg-copper-500/15` 高亮为"当前行"。

- [ ] **Step 1: 写失败测试 `src/test/transcript-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { TranscriptCard } from "../components/dashboard/TranscriptCard"
import { mockDashboard } from "../data/mock"

describe("TranscriptCard", () => {
  it("renders all segments with timestamps and speakers", () => {
    render(<TranscriptCard segments={mockDashboard.transcript} />)
    expect(screen.getByText("Transcript")).toBeInTheDocument()
    expect(screen.getByText("00:00:00")).toBeInTheDocument()
    expect(screen.getByText("00:00:09")).toBeInTheDocument()
    expect(screen.getByText(/Welcome back to the AI Podcast!/)).toBeInTheDocument()
    expect(screen.getByText(/Exactly. Bias, transparency/)).toBeInTheDocument()
    expect(screen.getAllByText("Alex:")).toHaveLength(3)
    expect(screen.getAllByText("Sam:")).toHaveLength(3)
  })

  it("highlights the first segment as current", () => {
    render(<TranscriptCard segments={mockDashboard.transcript} />)
    const first = screen.getByText(/Welcome back to the AI Podcast!/).closest("li")
    expect(first?.className).toContain("bg-copper-500/15")
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/transcript-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/TranscriptCard.tsx`**

```tsx
import { ListFilter, Search, SlidersHorizontal } from "lucide-react"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { TranscriptSegment } from "@/data/types"
import { formatTimestamp } from "@/lib/format"

export function TranscriptCard({ segments }: { segments: TranscriptSegment[] }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="shrink-0 text-base font-semibold text-gold">Transcript</h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-warm/60 bg-raised px-3 py-1.5 text-muted-warm">
            <Search className="size-3.5" />
            <input
              className="w-32 bg-transparent text-xs text-cream outline-none placeholder:text-muted-warm"
              placeholder="Search transcript..."
            />
          </div>
          <SlidersHorizontal className="size-4 text-muted-warm" />
          <ListFilter className="size-4 text-muted-warm" />
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <ol className="flex flex-col gap-1 pr-3">
          {segments.map((seg, i) => (
            <li key={seg.id} className={`flex gap-3 rounded-lg px-2 py-1.5 ${i === 0 ? "bg-copper-500/15" : ""}`}>
              <span className="tnum w-20 shrink-0 pt-0.5 text-xs text-copper-300">{formatTimestamp(seg.startSec)}</span>
              <p className="text-sm leading-relaxed">
                <span className="font-semibold text-cream">{seg.speaker}: </span>
                <span className="text-cream/80">{seg.text}</span>
              </p>
            </li>
          ))}
        </ol>
      </ScrollArea>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/transcript-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/TranscriptCard.tsx frontend/src/test/transcript-card.test.tsx
git commit -m "feat(frontend): add TranscriptCard panel"
```

---

### Task 10: ChaptersCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/ChaptersCard.tsx`
- Test: `frontend/src/test/chapters-card.test.tsx`

**Interfaces:**
- Consumes: `Chapter`（`@/data/types`）、`formatSeconds`（`@/lib/format`）、`Card` `Button` `ScrollArea`。
- Produces: `ChaptersCard({ chapters }: { chapters: Chapter[] })`。

- [ ] **Step 1: 写失败测试 `src/test/chapters-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { ChaptersCard } from "../components/dashboard/ChaptersCard"
import { mockDashboard } from "../data/mock"

describe("ChaptersCard", () => {
  it("renders all chapters with timestamps and durations", () => {
    render(<ChaptersCard chapters={mockDashboard.chapters} />)
    expect(screen.getByText(/Chapters/)).toBeInTheDocument()
    expect(screen.getByText("Add Chapter")).toBeInTheDocument()
    for (const title of [
      "Introduction & Welcome",
      "AI in Healthcare",
      "Creative AI Revolution",
      "Ethical Considerations",
      "The Future of AI",
      "Q&A and Closing Thoughts",
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
    expect(screen.getByText("11:57")).toBeInTheDocument()
    expect(screen.getByText("23:22")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/chapters-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/ChaptersCard.tsx`**

```tsx
import { Bookmark, MoreVertical, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { Chapter } from "@/data/types"
import { formatSeconds } from "@/lib/format"

export function ChaptersCard({ chapters }: { chapters: Chapter[] }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-gold">Chapters &amp; Highlights</h2>
        <Button size="sm" className="bg-copper-gradient text-[#1A0E04] hover:opacity-90">
          <Plus className="size-4" />
          Add Chapter
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <ol className="flex flex-col gap-2 pr-3">
          {chapters.map((ch) => (
            <li key={ch.id} className="flex items-center gap-3 rounded-xl bg-raised/60 p-2">
              <img src={ch.thumbnailUrl} alt="" className="size-11 shrink-0 rounded-lg object-cover" />
              <span className="tnum w-11 shrink-0 text-xs text-copper-300">{formatSeconds(ch.startSec)}</span>
              <span className="min-w-0 flex-1 truncate text-sm">{ch.title}</span>
              <span className="tnum shrink-0 text-xs text-muted-warm">{formatSeconds(ch.durationSec)}</span>
              <Bookmark className="size-4 shrink-0 text-muted-warm" />
              <MoreVertical className="size-4 shrink-0 text-muted-warm" />
            </li>
          ))}
        </ol>
      </ScrollArea>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/chapters-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/ChaptersCard.tsx frontend/src/test/chapters-card.test.tsx
git commit -m "feat(frontend): add ChaptersCard panel"
```

---

### Task 11: RecentProjectsCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/RecentProjectsCard.tsx`
- Test: `frontend/src/test/recent-projects-card.test.tsx`

**Interfaces:**
- Consumes: `Project`（`@/data/types`）、`formatDate` `formatSeconds`（`@/lib/format`）、`Card` `Badge`。
- Produces: `RecentProjectsCard({ projects }: { projects: Project[] })`。

- [ ] **Step 1: 写失败测试 `src/test/recent-projects-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { RecentProjectsCard } from "../components/dashboard/RecentProjectsCard"
import { mockDashboard } from "../data/mock"

describe("RecentProjectsCard", () => {
  it("renders four project cards with status badges", () => {
    render(<RecentProjectsCard projects={mockDashboard.recentProjects} />)
    expect(screen.getByText("Recent Projects")).toBeInTheDocument()
    expect(screen.getByText("View all")).toBeInTheDocument()
    expect(screen.getByText("Product Launch Talk")).toBeInTheDocument()
    expect(screen.getByText("Customer Interview #7")).toBeInTheDocument()
    expect(screen.getByText("Design Sprint Debrief")).toBeInTheDocument()
    expect(screen.getAllByText("Completed")).toHaveLength(3)
    expect(screen.getByText("Processing")).toBeInTheDocument()
    expect(screen.getByText("34:18")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/recent-projects-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/RecentProjectsCard.tsx`**

```tsx
import { MoreVertical } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import type { Project } from "@/data/types"
import { formatDate, formatSeconds } from "@/lib/format"

export function RecentProjectsCard({ projects }: { projects: Project[] }) {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gold">Recent Projects</h2>
        <button className="text-xs text-copper-300 transition-colors hover:text-copper-500">View all</button>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-4 gap-3">
        {projects.map((p) => (
          <div key={p.id} className="flex min-w-0 flex-col overflow-hidden rounded-xl bg-raised/60">
            <img src={p.thumbnailUrl} alt="" className="h-14 w-full shrink-0 object-cover" />
            <div className="flex min-h-0 flex-1 flex-col gap-0.5 p-2">
              <p className="truncate text-xs font-medium">{p.title}</p>
              <p className="tnum text-[10px] text-muted-warm">
                {formatDate(p.createdAt)} · {formatSeconds(p.durationSec)}
              </p>
              <div className="mt-auto flex items-center justify-between pt-1">
                {p.status === "completed" ? (
                  <Badge className="border-success/40 bg-success/15 px-1.5 py-0 text-[10px] text-success">Completed</Badge>
                ) : (
                  <Badge className="border-copper-500/40 bg-copper-500/15 px-1.5 py-0 text-[10px] text-copper-300">Processing</Badge>
                )}
                <MoreVertical className="size-3.5 text-muted-warm" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/recent-projects-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/RecentProjectsCard.tsx frontend/src/test/recent-projects-card.test.tsx
git commit -m "feat(frontend): add RecentProjectsCard panel"
```

---

### Task 12: ExportCard 面板

**Files:**
- Create: `frontend/src/components/dashboard/ExportCard.tsx`
- Test: `frontend/src/test/export-card.test.tsx`

**Interfaces:**
- Consumes: `Card`。
- Produces: `ExportCard()`（无 props；按钮本阶段为静态展示，不接导出逻辑）。

- [ ] **Step 1: 写失败测试 `src/test/export-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { ExportCard } from "../components/dashboard/ExportCard"

describe("ExportCard", () => {
  it("renders three export formats", () => {
    render(<ExportCard />)
    expect(screen.getByText("Export")).toBeInTheDocument()
    expect(screen.getByText("Export your transcript or summary")).toBeInTheDocument()
    for (const name of ["TXT", "SRT", "MD"]) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getByText(/More formats coming soon/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/export-card.test.tsx
```
预期：FAIL。

- [ ] **Step 3: 写 `src/components/dashboard/ExportCard.tsx`**

```tsx
import { Captions, FileCode, FileText } from "lucide-react"
import { Card } from "@/components/ui/card"

const FORMATS = [
  { icon: FileText, name: "TXT", ext: ".txt" },
  { icon: Captions, name: "SRT", ext: ".srt" },
  { icon: FileCode, name: "MD", ext: ".md" },
]

export function ExportCard() {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div>
        <h2 className="text-base font-semibold text-gold">Export</h2>
        <p className="text-xs text-muted-warm">Export your transcript or summary</p>
      </div>
      <div className="grid flex-1 grid-cols-3 gap-3">
        {FORMATS.map(({ icon: Icon, name, ext }) => (
          <button
            key={name}
            className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-warm/60 bg-raised transition-colors hover:border-copper-500/60"
          >
            <Icon className="size-6 text-copper-300" strokeWidth={1.5} />
            <span className="text-sm font-semibold">{name}</span>
            <span className="text-[10px] text-muted-warm">{ext}</span>
          </button>
        ))}
      </div>
      <p className="text-center text-[10px] text-muted-warm/70">+ More formats coming soon</p>
    </Card>
  )
}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/export-card.test.tsx
```
预期：PASS。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/components/dashboard/ExportCard.tsx frontend/src/test/export-card.test.tsx
git commit -m "feat(frontend): add ExportCard panel"
```

---

### Task 13: DashboardPage 组装（12 列栅格 + MockProvider 接线）

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx`（整个重写）
- Test: `frontend/src/test/dashboard-page.test.tsx`

**Interfaces:**
- Consumes: `dataProvider.getDashboard()`（Task 4）；7 个面板组件（Task 6–12）。
- Produces: 完整 Dashboard 视图；栅格：第 1 行 4/4/4，第 2 行 7/5，第 3 行 7/5（`grid-cols-12 gap-4 p-6`）。

- [ ] **Step 1: 写失败测试 `src/test/dashboard-page.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { DashboardPage } from "../pages/DashboardPage"

describe("DashboardPage", () => {
  it("loads provider data and renders all seven panels", async () => {
    render(<DashboardPage />)
    expect(await screen.findByText("AI Podcast Episode 12")).toBeInTheDocument()
    expect(screen.getByText("Upload a video or audio file")).toBeInTheDocument()
    expect(screen.getByText(/AI Summary/)).toBeInTheDocument()
    expect(screen.getByText("Transcript")).toBeInTheDocument()
    expect(screen.getByText(/Chapters/)).toBeInTheDocument()
    expect(screen.getByText("Recent Projects")).toBeInTheDocument()
    expect(screen.getByText("Export")).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run src/test/dashboard-page.test.tsx
```
预期：FAIL（当前简版没有 7 个面板）。

- [ ] **Step 3: 重写 `src/pages/DashboardPage.tsx`**

```tsx
import { useEffect, useState } from "react"
import { ChaptersCard } from "@/components/dashboard/ChaptersCard"
import { ExportCard } from "@/components/dashboard/ExportCard"
import { PlayerCard } from "@/components/dashboard/PlayerCard"
import { RecentProjectsCard } from "@/components/dashboard/RecentProjectsCard"
import { SummaryCard } from "@/components/dashboard/SummaryCard"
import { TranscriptCard } from "@/components/dashboard/TranscriptCard"
import { UploadCard } from "@/components/dashboard/UploadCard"
import { dataProvider } from "@/data/provider"
import type { DashboardData } from "@/data/types"

export function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null)

  useEffect(() => {
    void dataProvider.getDashboard().then(setData)
  }, [])

  if (!data) {
    return <div className="p-6 text-sm text-muted-warm">Loading dashboard…</div>
  }

  return (
    <div className="grid grid-cols-12 gap-4 p-6">
      <div className="col-span-4 h-[248px]">
        <UploadCard />
      </div>
      <div className="col-span-4 h-[248px]">
        <PlayerCard episode={data.currentEpisode} />
      </div>
      <div className="col-span-4 h-[248px]">
        <SummaryCard summary={data.summary} />
      </div>
      <div className="col-span-7 h-[400px]">
        <TranscriptCard segments={data.transcript} />
      </div>
      <div className="col-span-5 h-[400px]">
        <ChaptersCard chapters={data.chapters} />
      </div>
      <div className="col-span-7 h-[190px]">
        <RecentProjectsCard projects={data.recentProjects} />
      </div>
      <div className="col-span-5 h-[190px]">
        <ExportCard />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 运行确认通过 + 构建**

```bash
cd /d/aaron/VIDA/frontend && npx vitest run && npm run build
```
预期：全部 PASS；build 零错误。

- [ ] **Step 5: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/pages/DashboardPage.tsx frontend/src/test/dashboard-page.test.tsx
git commit -m "feat(frontend): assemble dashboard page with seven panels"
```

---

### Task 14: AI 素材生成（汽水飞溅图，覆盖占位图）

> 本任务需要图像生成能力，由编排者（主会话）直接执行：调用 `image_generation` 技能逐张生成，
> 保存覆盖 `frontend/src/assets/` 下的同名占位 PNG。子代理无此技能，请勿派发给子代理。

**Files:**
- Modify（覆盖）: `frontend/src/assets/splash-sidebar.png`
- Modify（覆盖）: `frontend/src/assets/poster-episode.png`
- Modify（覆盖）: `frontend/src/assets/chapter-1.png` ~ `chapter-4.png`
- Modify（覆盖）: `frontend/src/assets/project-1.png` ~ `project-4.png`

**Interfaces:**
- Consumes: Task 1 创建的占位图文件名契约。
- Produces: 风格统一的 10 张 AI 生成图（文件路径不变，组件零改动）。

统一风格后缀（每张 prompt 末尾都拼接）：

```
amber sparkling soda beverage with rich golden bubbles, deep dark-brown black background,
cinematic studio lighting, copper-gold tones, photorealistic, high detail, no text, no logo
```

- [ ] **Step 1: 生成 `splash-sidebar.png`（竖版 2:3，1K）**

Prompt: `vertical shot of amber soda splashing upward from the bottom edge, droplets frozen mid-air, [统一风格后缀]`

- [ ] **Step 2: 生成 `poster-episode.png`（横版 16:9，2K）**

Prompt: `close-up of a glass filled with amber sparkling soda, vigorous fizz and splash, ice cubes, [统一风格后缀]`

- [ ] **Step 3: 生成章节缩略图 4 张（方形 1:1，1K）**

- `chapter-1.png`: `top-down shot of a glass of amber soda with bubbles rising, [统一风格后缀]`
- `chapter-2.png`: `side shot of a soda can being opened with fizz bursting out, [统一风格后缀]`
- `chapter-3.png`: `two glasses of amber soda clinking with a small splash, [统一风格后缀]`
- `chapter-4.png`: `macro shot of golden soda bubbles on glass surface, [统一风格后缀]`

- [ ] **Step 4: 生成项目封面 4 张（横版 3:2，1K）**

- `project-1.png`: `glass bottle of amber soda pouring into a glass with splash, [统一风格后缀]`
- `project-2.png`: `high-speed shot of soda wave curling inside a glass, [统一风格后缀]`
- `project-3.png`: `a sweating glass of amber soda on dark stone with droplets, [统一风格后缀]`
- `project-4.png`: `splash crown of amber soda from a dropped ice cube, [统一风格后缀]`

- [ ] **Step 5: 逐张验证**

每张生成后用 ReadMediaFile 检查：① 是琥珀色汽水而非咖啡；② 深色背景与色板协调；③ 无文字/水印。
不满意的调整 prompt 重生成（最多重试 2 次/张，仍不满意保留最接近的一张并在最终汇报中注明）。

- [ ] **Step 6: 构建确认引用完整**

```bash
cd /d/aaron/VIDA/frontend && npm run build
```
预期：build 零错误（图片路径未变，仅内容更新）。

- [ ] **Step 7: Commit**

```bash
cd /d/aaron/VIDA
git add frontend/src/assets
git commit -m "feat(frontend): add AI-generated soda splash artwork"
```

---

### Task 15: 最终验收（测试 + 构建 + 视觉比对）

**Files:**
- 无新增文件（仅验证）。

**Interfaces:**
- Consumes: 全部前序任务。

- [ ] **Step 1: 全量测试 + 构建**

```bash
cd /d/aaron/VIDA/frontend
npx vitest run
npm run build
```
预期：全部 PASS（≥ 12 个测试）；build 零 TS 错误。

- [ ] **Step 2: 临时启动 dev 服务器做视觉验收（验完必须停止）**

```bash
cd /d/aaron/VIDA/frontend && npm run dev
```
在 1440×900 视口打开 `http://localhost:7100/`，与设计图逐项比对：

- [ ] 侧栏：金色渐变 VIDA Logo + 副标题、5 个导航项、Dashboard 铜金渐变激活态、底部汽水图 + 标语
- [ ] 顶栏：居中搜索框（placeholder 与 ⌘K）
- [ ] 7 个面板全部出现，栅格比例 4/4/4、7/5、7/5，间距均匀
- [ ] 卡片：深棕底 + 暖棕描边 + 圆角；铜金渐变按钮
- [ ] Transcript：铜色时间戳、说话人、首行高亮、区域内滚动
- [ ] Chapters：缩略图 + 时间戳 + 时长 + Add Chapter 按钮
- [ ] Recent Projects：4 张卡、3× Completed + 1× Processing 徽章
- [ ] Export：TXT / SRT / MD 三个大图标按钮
- [ ] `/transcribe` 等 4 个路由可切换并显示占位页；`/` 重定向到 `/dashboard`
- [ ] 窄于 1280px 显示英文桌面端提示

验证后**停止 dev 服务器**（Ctrl+C），不得后台留驻。

- [ ] **Step 3: 如视觉偏差，修复并回归 Step 1；通过后最终 Commit**

```bash
cd /d/aaron/VIDA
git add -A frontend
git commit -m "feat(frontend): VIDA 2.0 dashboard visual acceptance pass" || echo "nothing to commit"
```

---

## Self-Review 记录

- **Spec 覆盖**：设计文档 §2（结构）→ Task 1/5；§3（令牌）→ Task 2；§4（布局/组件）→ Task 5–13；§5（数据/Mock）→ Task 4/13；§6（API 契约）→ 交接文档，不在前端计划内；§7（素材）→ Task 1 占位 + Task 14 生成；§8（边界）→ Task 5 窄屏兜底；§9（测试/验收）→ 各 Task 测试 + Task 15。
- **有意偏离**：Tailwind v4（无 tailwind.config.ts，令牌集中在 tokens.css）；shadcn 仅装 button/card/badge/scroll-area；`--color-card` 等通过 `@theme inline` 映射而非重复定义，避免冲突。
- **类型一致性**：面板 props 与 `data/types.ts` 字段逐一核对一致；`formatSeconds`/`formatTimestamp`/`formatDate` 签名在各 Task 间一致；素材文件名在 Task 1/4/5/14 间一致。
