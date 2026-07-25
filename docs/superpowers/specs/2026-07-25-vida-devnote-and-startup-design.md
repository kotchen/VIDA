# VIDA 开发记录与本地启动说明设计

## 目标

为本次 VIDA v2 前端集成补充可追溯的开发记录，并在仓库根目录建立统一的本地开发入口说明。
文档完成后，实际启动前端与后端并验证开发地址可访问。

## 文档布局

- 本次记录：`docs/devnote/2026-07-0725/vida-v2-frontend-integration.md`
- 仓库级开发指南：`AGENTS.md`
- 后端 v2 专项规则继续由 `backend/v2/AGENTS.md` 维护，根目录文件不复制其领域细节。

## Devnote 内容

本次 devnote 使用中文，至少包括：

1. 开发内容：v2 API 客户端、Provider Profile、Episode 生命周期与删除、SSE 事件推送与降级轮询、
   内容操作、页面接入、部署和测试结果。
2. 学习与沉淀：REST 作为事实来源、SSE 只做失效通知、终态删除约束、同源生产部署、
   前后端契约和测试策略。
3. 回滚方式：提供按提交范围回滚的安全步骤，优先使用 `git revert`，并说明数据和数据库 migration
   不应通过 Git 命令直接回退。

文档还记录已知限制：Docker 镜像构建与容器冒烟测试尚未完成。

## 根目录 AGENTS.md

根目录指南包括：

- Python、Node.js、FFmpeg、环境变量和依赖安装；
- 后端开发启动命令及 `8000` 端口；
- 前端 Vite 启动命令及 `7100` 端口；
- 旧版、v2、API、SSE 和 Vite 开发地址；
- 服务停止和前后端验证命令；
- 单 FastAPI/Uvicorn 进程约束；
- 强制 devnote 规则：每次开发都在 `docs/devnote/YYYY-MM-MMDD/` 下新增具有描述性文件名的
  Markdown 文件，并包含“开发内容”“学习与可沉淀经验”“回滚操作”三个章节。

## 启动与验收

从仓库根目录分别启动：

- 后端：加载 `.env` 后通过项目 Python 虚拟环境运行 `python start.py --prod`；
- 前端：在 `frontend/` 执行 `npm run dev -- --host 127.0.0.1`。

验收以下地址：

- `http://127.0.0.1:8000/api/v2/dashboard`
- `http://127.0.0.1:8000/v2`
- `http://127.0.0.1:7100/v2/`

若依赖或必要配置缺失，先按根目录指南完成安装或生成本地开发配置，不把密钥写入 Git。

## 边界

- 本次不新增一键启动脚本。
- 不修改应用运行逻辑。
- 不重写 `backend/v2/AGENTS.md` 中已有的后端专项约束。
