# VIDA 仓库开发指南

## 适用范围与事实来源

本文件适用于整个仓库。进入子目录工作时，还必须遵守距离目标文件最近的 `AGENTS.md`；
例如 `backend/v2/AGENTS.md` 包含 v2 后端的领域、存储、安全和测试约束。

发生不一致时，按以下优先级判断：

1. 当前代码和自动化测试；
2. `docs/api/v2-api-contract.md`；
3. 根目录 README、开发规格与历史计划。

不要为了 v2 改动破坏 VIDA 1.x 的 `/`、`/static/*`、既有 `/api/*`、`temp/` 或
`temp/tasks.json` 行为。

## 强制 Devnote 规则

每次开发都必须留下 devnote，包括功能开发、缺陷修复、重要重构、公开配置或运行方式变更。

- 路径格式：`docs/devnote/YYYY-MM-MMDD/`，例如 2026 年 7 月 25 日使用
  `docs/devnote/2026-07-0725/`。
- 一个开发主题对应一个具有描述性的 Markdown 文件名，例如
  `vida-v2-frontend-integration.md`；不要使用 `README.md`、`note.md` 等泛化名称。
- 同一天继续同一个开发主题时更新原文件；不同主题新建文件。
- 每条 devnote 至少包含以下三个一级内容章节：
  1. `开发内容`：目标、关键实现、影响范围、验证结果和已知限制；
  2. `学习与可沉淀经验`：设计判断、踩坑、可复用模式和后续建议；
  3. `回滚操作`：停止服务、备份要求、Git 回滚命令、数据/migration 注意事项和回滚后验证。
- 提交代码前运行 `git diff --check`，确认 devnote 与本次开发一起提交。
- 不在 devnote 中记录 API Key、主密钥、访问令牌、完整敏感 URL、数据库内容或用户数据。

## 环境要求

- Python 3.12；
- Node.js 和 npm（前端使用 `frontend/package-lock.json` 锁定依赖）；
- FFmpeg 与 ffprobe；
- 单个 FastAPI/Uvicorn 进程。不要使用多个 Uvicorn worker；v2 并发由
  `V2_MAX_CONCURRENT_JOBS` 控制。

首次安装后端依赖：

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

首次安装或锁文件变化后安装前端依赖：

```bash
cd frontend
npm ci
```

## 本地配置

v2 启动必须提供 `VIDA_PROFILE_MASTER_KEY`。它是 URL-safe Base64 字符串，解码后恰好为
32 字节。首次设置时只生成一次：

```bash
source venv/bin/activate
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
cp .env.example .env
```

把生成值写入未纳入 Git 的 `.env`：

```dotenv
VIDA_PROFILE_MASTER_KEY=<生成并长期保存的值>
V2_MAX_CONCURRENT_JOBS=2
V2_UPLOAD_MAX_GB=5
WHISPER_MODEL_SIZE=base
WHISPER_MODEL_LOAD_TIMEOUT_SEC=300
HF_HUB_DISABLE_XET=1
HF_HUB_ETAG_TIMEOUT=10
HF_HUB_DOWNLOAD_TIMEOUT=60
```

`start.py` 不会自行读取 `.env`，启动前必须由 shell、IDE 或密钥管理器注入。不要把真实主密钥或
Provider API Key 写入源码、命令参数、Git、devnote 或日志。`VIDA_PROFILE_MASTER_KEY` 必须与
完整 `data/v2/` 作为同一个备份和恢复单元；已有 Provider Profile 数据后不要随意更换。

## 启动后端

在终端 1，从仓库根目录执行：

```bash
source venv/bin/activate
set -a
source .env
set +a
python start.py --prod
```

- 必须先执行 `source venv/bin/activate`，不能用 `venv/bin/python start.py --prod` 代替。后者虽然会
  使用虚拟环境中的 Python 包，但不会把 `venv/bin` 加入子进程的 `PATH`；URL 采集任务调用
  `yt-dlp` 时会因找不到可执行文件而在 5% 进度失败。
- 启动后可用 `command -v python` 和 `command -v yt-dlp` 检查两者是否都指向当前仓库的
  `venv/bin/`。
- FastAPI 会在启动阶段下载并加载 `WHISPER_MODEL_SIZE` 对应的模型，成功后才启动 v2 scheduler
  并对外就绪。默认 `base` 首次需要从 Hugging Face 下载约 145 MB，首次启动会明显更慢。
- 默认禁用 Hugging Face Xet 并使用普通 HTTPS；除非部署者已经验证 Xet 与当前代理兼容，否则不要
  覆盖 `HF_HUB_DISABLE_XET=1`。模型初始化失败或超过
  `WHISPER_MODEL_LOAD_TIMEOUT_SEC` 时服务不会就绪，scheduler worker 也不会启动。
- 生产部署应尽量持久化 Hugging Face cache，避免容器重建或服务迁移时重复下载模型；不要把模型
  文件提交进 Git。
- 后端端口：`8000`；
- `--prod` 会关闭热重载，适合长任务和稳定 SSE 连接；
- 本地需要调试 Python 热重载时可去掉 `--prod`；
- 按 `Ctrl+C` 停止；
- 不要同时启动第二个 FastAPI/Uvicorn 进程。

如果没有 `.env`，必须先按“本地配置”生成，不要用新的临时主密钥启动已有 `data/v2/`。

## 启动前端

在终端 2，从仓库根目录执行：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

- Vite 端口：`7100`；
- `/api` 自动代理到 `http://127.0.0.1:8000`；
- 按 `Ctrl+C` 停止；
- 日常重复启动且 `package-lock.json` 未变化时，可省略 `npm ci`。

## 本地访问地址

- 旧版 UI：`http://127.0.0.1:8000/`
- FastAPI 托管的 VIDA 2.0：`http://127.0.0.1:8000/v2`（当前版本不要添加尾斜杠）
- Vite 开发页面：`http://127.0.0.1:7100/v2/`
- v2 Dashboard API：`http://127.0.0.1:8000/api/v2/dashboard`
- Vite 代理 API：`http://127.0.0.1:7100/api/v2/dashboard`
- SSE：`http://127.0.0.1:8000/api/v2/events`

快速检查：

```bash
curl --fail http://127.0.0.1:8000/api/v2/dashboard
curl --fail http://127.0.0.1:8000/v2
curl --fail http://127.0.0.1:7100/v2/
curl --fail http://127.0.0.1:7100/api/v2/dashboard
```

SSE 是长连接，检查时设置超时：

```bash
curl --no-buffer --max-time 5 http://127.0.0.1:8000/api/v2/events
```

## 常用验证命令

后端（仓库根目录）：

```bash
source venv/bin/activate
python -m unittest discover tests
```

前端：

```bash
cd frontend
npm test
npm run lint
npm run build
```

公开 API、配置、数据布局、启动恢复或前端路由发生变化时，同步更新对应契约、README、测试和
devnote。结束任务前必须报告实际运行的验证命令及结果；没有完成的 Docker 或外部服务验证要明确
标注，不能推断为通过。
