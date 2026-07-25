# VIDA V2 后端开发指南

## 适用范围

本文件适用于 `backend/v2/` 及其子目录。V2 是挂载在现有 FastAPI 应用中的模块化单体，
所有公开接口位于 `/api/v2`。修改 V2 时必须继续保持 VIDA 1.x 的 `/`、`/static/*`、
既有 `/api/*`、`temp/` 和 `temp/tasks.json` 行为不变。

以下内容按优先级作为事实来源：

1. 当前实现和 `tests/v2/`；
2. `docs/api/v2-api-contract.md`；
3. `docs/superpowers/specs/2026-07-18-vida-v2-backend-design.md` 与历史 plan。

历史设计文档可能包含已经调整过的文件名或实现细节。遇到不一致时，以代码和测试为准；
如果修改公开行为，同时更新 API 契约和契约测试。

## 开发环境与安装

### 环境依赖

- Python 3.12。Docker 镜像和当前后端设计均以 3.12 为基准；本地开发也应使用相同版本。
- FFmpeg 与 ffprobe。上传媒体、URL 来源探测、转码和音频提取都会使用它们。
- Python 依赖统一安装自仓库根目录的 `requirements.txt`，主要包括 FastAPI、Uvicorn、
  Pydantic 2、python-multipart、yt-dlp、faster-whisper、OpenAI SDK、cryptography 和 httpx。
- 安装 Python 包和首次下载 Whisper 模型需要网络；运行 URL 下载和 Provider 测试同样需要网络。
- `data/v2/` 会保存 SQLite、源媒体和生成产物。开发机需预留足够磁盘空间；Whisper 模型推理还
  需要与所选 `WHISPER_MODEL_SIZE` 匹配的内存，默认模型为 `base`。

本地安装前先确认：

```bash
python3 --version
ffmpeg -version
ffprobe -version
```

macOS 可通过 Homebrew 安装 FFmpeg：

```bash
brew install ffmpeg
```

Ubuntu/Debian 可使用：

```bash
sudo apt update
sudo apt install -y ffmpeg
```

### 本地虚拟环境安装

从仓库根目录执行：

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

Windows PowerShell 激活方式为：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

V2 必须配置 `VIDA_PROFILE_MASTER_KEY`。它是一个 URL-safe Base64 字符串，解码后恰好 32
字节。首次部署只生成一次，并与完整的 `data/v2/` 一起备份：

```bash
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

推荐复制环境模板并将生成值写入未纳入 Git 的 `.env`：

```bash
cp .env.example .env
```

关键配置如下：

```dotenv
VIDA_PROFILE_MASTER_KEY=<首次生成且长期保持不变的值>
V2_MAX_CONCURRENT_JOBS=2
V2_UPLOAD_MAX_GB=5
WHISPER_MODEL_SIZE=base
```

`OPENAI_API_KEY`、`OPENAI_BASE_URL` 是 1.x 服务端默认 Provider 配置；V2 Provider 凭据通过
`/api/v2/provider-profiles` 管理，不应写入源码。`.env` 仅由 Docker Compose 自动读取；
直接运行 `start.py` 前需由 shell、IDE 或密钥管理器把配置注入进程环境。例如在 POSIX shell
中加载根目录 `.env`：

```bash
set -a
source .env
set +a
python3 start.py
```

生产式本地启动使用：

```bash
python3 start.py --prod
```

不要把真实主密钥或 Provider API Key 写入命令历史、日志或提交到 Git。丢失或随意轮换
`VIDA_PROFILE_MASTER_KEY` 会导致已保存的 Provider 凭据无法解密。

### Docker 安装

Docker Compose 是最直接且环境最一致的安装方式。根目录 `Dockerfile` 基于
`python:3.12-slim-bookworm`，并安装 FFmpeg、构建工具及 `requirements.txt`：

```bash
cp .env.example .env
# 在 .env 中设置 VIDA_PROFILE_MASTER_KEY
docker compose up -d --build
```

启动后访问 `http://localhost:8000`。Compose 将宿主机 `./data/v2` 挂载到容器
`/app/data/v2`；不要删除该目录或只备份 SQLite 文件。停止服务但保留数据：

```bash
docker compose down
```

也可直接构建和运行：

```bash
docker build -t vida .
docker run --rm -p 8000:8000 --env-file .env \
  -v "$(pwd)/data/v2:/app/data/v2" vida
```

生产环境只运行一个应用容器/一个 Uvicorn 进程。不要使用 `--workers` 或横向复制该服务；
V2 的并发由 `V2_MAX_CONCURRENT_JOBS` 控制。

## 运行模型与目录结构

- `bootstrap.py`：构建生产/测试 runtime，组装 Repository、Service、Pipeline 和 Scheduler。
- `container.py`：`V2Runtime` 生命周期；启动时初始化、恢复任务、清理孤儿文件并启动 worker。
- `router.py`、`api/`：FastAPI 路由、请求解析、状态码、HTTP Range/缓存语义和响应转换。
- `schemas.py`：Pydantic 2 请求/响应模型，统一输出 `camelCase`。
- `domain.py`：不可变领域记录、公开状态和领域校验。
- `services/`：业务规则、上传解析、Dashboard、导出、媒体文件生命周期和 Provider 凭据服务。
- `repositories/`：持久化与跨表事务；业务代码不得绕过 Repository 直接操作 SQLite。
- `jobs/`：持久 FIFO 调度、协作取消、来源获取、转录与 AI 处理管线。
- `database.py`、`migrations/`：SQLite 连接、WAL、显式事务和版本化 SQL migration。

生产数据固定在项目根目录的 `data/v2/`：

```text
data/v2/
├── vida-v2.sqlite3
└── episodes/{episodeId}/
```

不要读取、迁移或写入 1.x 的 `temp/` 数据。数据库只保存结构化数据和受控相对路径，大文件保存在
V2 数据目录。第一版仅支持单个 FastAPI/Uvicorn 进程；并发由进程内 Scheduler worker 控制，
不要通过增加 Uvicorn worker 扩容。

## 分层约束

- API 层负责协议边界，不写 SQL，不直接拼接磁盘路径，也不承载队列状态转换。
- Service 层负责提交、重试、取消、重新生成、章节规则及文件生命周期等用例。
- Repository 层负责 SQL、事务和领域记录映射。需要原子更新的 Episode/Job/产物状态必须在同一
  数据库事务内完成。
- Scheduler 和 Pipeline 不依赖 FastAPI Request；它们通过 Repository 和明确的协议对象协作。
- 新依赖优先通过构造函数注入，并在 `build_test_runtime()` 中保持可替换，避免隐藏的全局状态。
- 保持 `from __future__ import annotations`、类型标注、不可变 dataclass 和当前相对导入风格。
  `bootstrap.py` 还必须兼容 `backend.v2` 与从 `backend/` 启动后的顶层 `v2` 导入。

## 不可破坏的领域与存储规则

- Episode 和 Job 的公开状态仅为
  `queued | processing | completed | failed | canceled`。
  `cancel_requested_at` 是内部协作取消标志，不是新的公开状态。
- Job 是全局持久 FIFO：按 `submitted_at`、`id` 排序；同一 Episode 同时最多一个
  queued/processing Job。重试创建新 attempt 并进入队尾。
- 服务重启时，遗留 processing Job 使用原 Job ID 重新排队，从管线开头执行，不创建重复 attempt。
- 主处理 Job 的 Episode 状态、进度、错误和 `current_job_id` 必须保持事务一致。独立 summary/
  chapters regeneration 不得把已完成 Episode 改回 processing。
- 优化、摘要和自动章节失败是 typed warning，不应让已成功转录的 Episode 失败；来源获取、转录
  或核心持久化失败才使主处理任务失败。
- 重新生成摘要必须先生成完整新值，再原子替换；失败保留旧摘要。重新生成章节只替换
  `source = generated` 的章节，并始终保留 manual 章节。
- Provider Profile 更新必须创建不可变 revision。Job 入队时固定
  `provider_profile_revision_id`，后续更新或逻辑删除 Profile 不得改变已入队任务。
- Provider API Key 只允许以 AES-256-GCM 密文落库。不得在响应、日志、异常、Job 或测试快照中
  暴露明文。`VIDA_PROFILE_MASTER_KEY` 缺失或无效时拒绝启动，不允许明文降级。
- 已应用 migration 不做就地修改。数据库结构变化新增递增编号的
  `NNN_description.sql`，保持初始化幂等、事务安全，并补 migration/兼容性测试。

## API 契约

- JSON 字段使用 `camelCase`；内部 Python/SQL 使用 `snake_case`。
- 时间使用 UTC ISO 8601；`*Sec` 单位为秒；`progress` 为 0–100 整数；
  `queuePosition` 从 1 开始且只对 queued 记录非空。
- 创建 Episode 返回 `202 Accepted` 和正确的 `Location`。
- 所有 V2 响应都必须带 `X-Request-ID`。所有 V2 错误必须使用：

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

- 业务失败使用 `V2Error` 和稳定、无敏感信息的错误码/消息；不要把原始上游响应或异常文本返回给
  客户端。新增或修改路由、method、状态码、请求/响应字段时，同步修改
  `docs/api/v2-api-contract.md`、Pydantic schema 和 `tests/v2/test_legacy_api_contract.py`。
- 媒体接口必须继续满足 GET/HEAD、单 Range、条件请求、ETag、Last-Modified、Content-Length
  与 304/412/416 语义；不要用普通 `FileResponse` 绕过现有受控文件打开和资源所有权逻辑。

## 异步、取消与资源所有权

- 不在事件循环中直接执行 sqlite3、模型推理、阻塞文件 I/O 或阻塞 subprocess 操作。
- 不要随意用裸 `asyncio.to_thread()` 替换现有 `jobs/blocking.py`、Scheduler repository task
  跟踪或管线中的取消安全 helper。awaiting task 被取消后，底层线程不会自动停止；必须等待其完成
  并在安全边界清理资源。
- 长阶段要保留协作取消检查和进度更新。`JobCanceled` 表示取消已到达安全边界；单个 Job 异常
  不得终止 worker 循环。
- staging 文件只能在完整校验后原子提交。上传中断、超限、解析失败、任务取消或处理失败时，
  必须清理 `.part`、attempt 目录和未提交文件。

## 安全边界

以下区域的修改必须保留或加强现有安全测试，不能为了简化实现而绕过：

- URL 来源仅允许合规 HTTP(S)；拒绝凭据 URL、私网/回环/链路本地地址、危险端口、不安全重定向、
  DNS rebinding 和非公开解析结果。
- yt-dlp 只能通过受控 SSRF proxy/固定解析路径访问网络；子进程环境必须清除敏感代理和凭据变量。
- 上传必须流式限额，JSON body、multipart part 数、字段长度、扩展名和文件名都必须受控。
- 所有磁盘路径必须位于 `data/v2/` 内，拒绝绝对路径、`..`、symlink/reparse-point 逃逸和
  check-then-open 竞态；响应中只暴露 API URL，不暴露真实文件路径。
- 日志只记录安全的阶段名、异常类型和经过清洗的 traceback 元数据。不要记录 API Key、主密钥、
  Authorization/header、完整 URL query、Provider payload、数据库内容或原始异常消息。

## 修改与测试流程

1. 先定位最接近的 `tests/v2/test_*.py`，用失败测试固定期望行为或回归场景。
2. 在保持上述分层和事务边界的最小范围内实现。
3. 先运行相关测试模块，再运行完整测试发现；涉及安全、恢复、取消或并发时，额外运行对应安全/
   recovery 测试，必要时重复执行以发现竞态。
4. 若改变公开契约、配置、数据布局或恢复要求，同步更新 API 文档及 README/README_ZH。

常用命令（从仓库根目录执行）：

```bash
# 首次本地运行先按 README 创建并激活 venv、安装 requirements.txt
python -m unittest tests.v2.test_<module> -v
python -m unittest discover -s tests -v
```

未激活虚拟环境且系统没有 `python` 命令时使用 `python3`；测试解释器必须已经安装
`requirements.txt`。

测试应使用 `tempfile`、`build_test_runtime()`、fake executor/downloader/runner 和 mock client，
不得调用真实 Provider、下载真实媒体或依赖已有的 `data/v2/`。测试结束必须关闭
`TestClient`/runtime、等待 scheduler 和被拥有的后台操作完成，并清理临时文件。

高风险变更至少覆盖以下对应套件：

- 队列、状态机、并发、恢复：`test_job_repository.py`、`test_scheduler.py`、
  `test_job_recovery.py`、`test_end_to_end_queue.py`
- 上传与 URL 获取：`test_episode_submission_api.py`、`test_episode_submission_security.py`、
  `test_source_ingest.py`、`test_secure_download.py`、`test_source_ingest_security.py`
- 文件生命周期与媒体：`test_file_recovery.py`、`test_media_api.py`
- Provider 与秘密：`test_provider_profile_repository.py`、`test_provider_profile_api.py`、
  `test_secret_redaction.py`
- API/1.x 兼容：`test_error_contract.py`、`test_legacy_api_contract.py`
