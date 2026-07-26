# Whisper 启动就绪开发记录

日期：2026-07-26  
关联设计：`docs/superpowers/specs/2026-07-25-whisper-startup-readiness-design.md`  
关联计划：`docs/superpowers/plans/2026-07-26-whisper-startup-readiness.md`

## 1. 开发内容

### 1.1 问题定位

- Bilibili 音频已经通过受控 SSRF 下载完成，但 Episode 长时间停在 20% `Transcribing`。
- Job heartbeat 持续更新，只能证明 worker 仍存活，不能证明当前阻塞操作有进展。
- 进程采样、Hugging Face cache 和安全日志共同确认：默认 `base` 模型的大文件保持为 0 字节
  `.incomplete`，原生 Xet 传输在系统代理后的 TLS 握手失败并持续等待。
- 20% 是流水线进入转录阶段的粗粒度状态，不是模型下载或音频转录的实时百分比。

### 1.2 Hugging Face 传输与超时

- 新增独立的 `backend/whisper_environment.py`，在导入 `faster_whisper` 前为
  `HF_HUB_DISABLE_XET`、metadata timeout 和 download timeout 设置安全默认值。
- 默认禁用 Xet，改用 Hugging Face 普通 HTTPS 下载；使用 `setdefault` 保留部署者显式配置。
- `Transcriber` 读取 `WHISPER_MODEL_SIZE` 和 `WHISPER_MODEL_LOAD_TIMEOUT_SEC`，拒绝非数字、非有限、
  零和负数超时。
- 新增异步 `preload()`：不阻塞事件循环，限制整体等待时间，并将构造失败、网络失败和超时转换成
  不包含 URL、代理、cache 路径或上游正文的稳定错误。

### 1.3 FastAPI readiness 与共享模型

- VIDA 1.x 与 v2 生产流水线改为共享同一个 `Transcriber` 实例，避免重复加载模型和重复占用内存。
- v2 runtime 增加内部 readiness callback。FastAPI startup 必须先完成模型下载与加载，之后才进行
  scheduler 恢复并启动 worker；readiness 失败时服务不就绪且 worker 不会启动。
- 测试 runtime 和注入 fake executor 的测试路径不预加载真实模型，不依赖外部网络。
- `start.py`、直接 Uvicorn 模块导入、`.env.example`、Dockerfile 和 Compose 使用一致的 Xet 与超时
  默认值。

### 1.4 运维文档与验证

- 根目录和 v2 的 `AGENTS.md` 已说明：默认 `base` 首次约下载 145 MB、首次启动较慢、readiness
  失败的含义，以及生产环境持久化 Hugging Face cache 的建议。
- TDD 已覆盖默认值与显式覆盖、导入顺序、非法超时、preload 超时与脱敏、单次并发构造、readiness
  顺序、失败时不启动 scheduler、共享实例和启动器子进程环境。
- 隔离 worktree 修改前基线：350 个测试通过、1 个跳过。
- 实现后聚焦回归：79 个测试通过；完整后端套件：362 个测试通过、1 个跳过。
- 运行态恢复前使用新默认值进行独立 preload：普通 HTTPS 下载过程中模型文件持续增长，最终
  `model.bin` 为 145,217,532 字节、无 `.incomplete` 残留，并成功完成 CPU/int8 模型构造。
- 原卡住 Job 在处理恢复前已自行进入脱敏的失败终态，没有遗留 queued/processing Job；Episode
  保留，待新版本合并并完成 startup readiness 后通过既有 retry 接口重试。

## 2. 学习与可沉淀经验

### 2.1 阶段百分比不能替代子阶段可观测性

`Transcribing = 20%` 同时覆盖模型初始化和实际音频推理，用户看到的“卡住”无法直接定位。排查时应
结合持久化 message、文件大小/mtime、线程采样、网络连接和安全日志，而不能把 heartbeat 或固定
百分比当成实际吞吐。长期可以为模型 readiness 提供独立健康状态，但本次不扩展公开 API。

### 2.2 Heartbeat 是存活信号，不是进展信号

scheduler 可以在业务线程等待外部 I/O 时继续更新 heartbeat。恢复和监控逻辑应把“worker 存活”与
“阶段在合理时间内前进”分开判断；外部模型、下载器和 Provider 调用必须各自具备明确超时。

### 2.3 模型资产属于服务 readiness

只在首个业务任务里下载模型，会把部署问题伪装成用户任务失败，并占住有限 worker。把固定模型的
下载和加载前移到 FastAPI startup，可让启动失败直接反映环境问题，也确保队列开始消费时推理能力
已经可用。

### 2.4 原生传输扩展必须在目标代理环境验证

普通 HTTPS 可用不代表原生 CAS/Xet 客户端具有相同的系统代理和 TLS 行为。对本地代理、企业代理或
透明网关环境，应分别验证 Python HTTP 客户端与原生扩展；无法保证兼容时，使用行为更可观测、超时
更成熟的普通 HTTPS 路径。

### 2.5 共享重量级依赖应通过显式注入

v1/v2 各自调用 `Transcriber()` 不容易在代码审查中发现，但会造成两份模型和锁。由 composition
root 创建实例并注入两个消费者，既能共享内存，也能把 preload 作为明确的生命周期能力测试。

## 3. 回滚操作

### 3.1 回滚前准备

1. 停止后端和前端，避免回滚时继续领取 Job。
2. 备份完整 `data/v2/` 和与其匹配的 `VIDA_PROFILE_MASTER_KEY`；不得输出或写入 Git。
3. 记录当前提交和工作区：

   ```bash
   git status --short
   git rev-parse HEAD
   ```

Hugging Face cache 不属于 VIDA 数据库事务，不需要随 `data/v2/` 回滚，也不要为了代码回滚删除已完成
的模型文件。

### 3.2 Git 回滚

功能提交范围为 `814d96e^..2352e50`。在共享分支使用反向提交，不重写历史：

```bash
git switch main
git pull --ff-only
git revert --no-commit 814d96e^..2352e50
git commit -m "revert: restore lazy Whisper initialization"
```

该范围会撤销普通 HTTPS 默认值、Transcriber preload、共享实例、FastAPI readiness 顺序和运维配置。
设计、计划和本 devnote 可以保留为历史记录；若必须一起撤销，再分别 revert 对应文档提交。

### 3.3 数据与运行态注意事项

- 本功能没有数据库 migration，不需要修改 SQLite 或 Episode 数据。
- 回滚后既有 queued/processing Job 仍按原恢复规则处理；不要手工编辑 jobs 表。
- 必须继续使用原 `VIDA_PROFILE_MASTER_KEY`，不要因重启生成新主密钥。
- 有效 Whisper cache 可以继续使用；回滚后首次未缓存模型会重新在业务任务的 20% 阶段加载。

### 3.4 回滚后验证

```bash
source venv/bin/activate
python -m unittest discover tests
python start.py --prod
curl --fail http://127.0.0.1:8000/api/v2/dashboard
curl --fail http://127.0.0.1:7100/api/v2/dashboard
```

确认后端、前端代理和 Provider Profile 仍可读取，再根据目标版本决定是否重试失败或取消的 Episode。
