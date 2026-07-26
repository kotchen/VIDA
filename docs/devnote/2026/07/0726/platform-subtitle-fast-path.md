# 平台字幕快车道与出口代理配置

## 开发内容

**目标**：URL Episode 优先抓取平台已有字幕替代本地 Whisper 转录（字幕抓取为秒级，
Whisper 为下载+线性转录耗时），抓取语言跟随用户提交时的语言选择，简体中文与繁体中文
必须区分；未命中或失败自动回退 Whisper。

**关键实现**：

- `backend/v2/jobs/subtitles.py`（新增）：`PlatformSubtitleFetcher`。两轮 yt-dlp
  （list → 按偏好选轨 → 下载），YouTube 强制 `player_client=android`（web 客户端
  无 PO Token 会丢弃字幕）；人工字幕优先于自动字幕。语言偏好表：
  `zh-Hans → zh-Hans/zh-CN/zh-SG/zh`，`zh-Hant → zh-Hant/zh-TW/zh-HK`，简体绝不匹配
  繁体轨道，反之亦然；历史泛化的 `zh` 简体优先、繁体兜底；其他语言精确匹配后回退
  语种前缀（`en → en/en-*`）。解析器按内容识别 json3（事件时间重叠钳位到下一事件
  起点）、B 站原生 `body/from/to/content` JSON、WebVTT、SRT。
- `backend/v2/jobs/pipeline.py`：新增可选 `subtitles` 依赖。仅 `source_type == "url"`
  时先走 "Fetching platform subtitles" 阶段（进度 20%），返回 None 或异常时回退
  Whisper；两条路径产出的转录段结构一致，后续摘要/章节流程不变。
- `backend/transcriber.py`：VAD 兜底。实测 Silero VAD（含 VIDA 生产参数
  `min_silence 900ms/pad 300ms`）会把音乐类整段音频吞掉返回 0 段；现在 VAD 结果为空
  时自动关 VAD 重试一次。
- 出口代理：`V2_PLATFORM_EGRESS_PROXY`（可选）。本机 YouTube 直连被 DNS 污染
  （解析到无效 IP），只有系统代理（127.0.0.1:7897）可达；SSRF 固定通道按设计直连，
  导致 YouTube 媒体下载与字幕抓取在该网络下不可用（媒体下载为既有问题）。设置该变量后
  仅平台页面抓取（YtDlpDownloader、PlatformSubtitleFetcher）改走该代理，直连媒体
  URL 下载仍走 SSRF 通道；不设置则行为完全不变。配置校验拒绝凭据 URL、非标端口。
- 前端：`SubmissionForm` 语言选项改为 简体中文（zh-Hans）/ 繁體中文（zh-Hant）/
  English / 日本語 / Español；`preferences.ts` 默认值改 `zh-Hans` 并把存量 `zh`
  迁移为 `zh-Hans`；AI prompt 增加语言代码 → 语言名映射（`zh-Hant → Traditional
  Chinese` 等），summary/chapter 提示词使用可读语言名。
- 接线：`bootstrap.py` 构造 `PlatformSubtitleFetcher(secure_downloader,
  egress_proxy=settings.platform_egress_proxy)` 并注入 pipeline；`.env.example`、
  `AGENTS.md`、`docs/api/v2-api-contract.md` 同步更新。

**性能对比（同一 3:33 YouTube 视频，本机实测）**：字幕抓取约 8-10 秒（恒定），
本地方案音频下载 21s + faster-whisper base 转录 24s ≈ 45s（随时长线性增长）。
egress 模式下真实网络 e2e：`en` 人工字幕 61 段 / 9.7s 成功；zh/fr 因当日基准测试
频繁请求触发 YouTube 429，验证到回退路径正确返回 None。

**⚠️ 运维事故（如实记录）**：重启后端加载新代码时，发现机器上同时存在三代 uvicorn
进程（9:44 终端 start.py、11:02 和 14:47 两个 daemon）。清理过程中 master key 只以
环境变量形式存在于进程内存，最后一个持有进程退出后 key 不可恢复。已按预案处置：
生成新 `VIDA_PROFILE_MASTER_KEY` 并写入 repo 根 `.env`（0600，gitignored），删除
无法解密的 provider profile 行（`deepseek-v4-flash`，其 API Key 随旧密钥失效，
需在 Settings 重新录入）。Episode、转录、摘要、章节、任务历史均为明文存储，无损失。
此后端现在以 `.env` 注入方式运行，密钥首次有了持久化备份位置。

**验证结果**：

- 后端 `python -m unittest discover tests`：401 通过 1 跳过（新增字幕偏好/选轨/解析/
  fetcher、pipeline 快车道与回退、VAD 兜底、egress 配置与绕过 SSRF 等 30 个用例）。
- 前端 `npm test`：89 通过；`npm run lint` 0 error；`npm run build` 成功。
- 服务恢复：`/api/v2/dashboard`、`/api/v2/provider-profiles`（空列表）均正常。

**已知限制**：

- YouTube 字幕轨道的可用性受平台限流影响（429 时回退 Whisper，功能正确但无提速）。
- B 站视频若无 CC 字幕（多数只有弹幕），走 Whisper 兜底；danmaku 不作为转录来源。
- 既有 Episode 的 `provider_profile_id` 指向已删除的旧 profile，对它们执行 Regenerate
  会失败；新建 profile 后新提交不受影响。

## 学习与可沉淀经验

- **savesubs 类服务本质是平台字幕代理**：同样的能力 yt-dlp 原生就有（android 客户端
  可绕过 PO Token 限制），引入第三方反而增加 Cloudflare cookie 维护与单点故障。
  评估"换服务"前先拆解它真正做了什么。
- **YouTube ASR json3 没有滚动窗口重复**，直接拼接事件文本即可；但事件时长经常超过
  实际语音，end 必须钳位到下一事件起点，否则通不过单调性校验。
- **环境假设要验证**：本地 CLI 能访问 YouTube 不代表服务端直连可以——macOS 系统代理
  对 Python 直连 socket 不生效。涉及网络的特性要在目标运行环境（SSRF 通道内）实测。
- **只存在于进程环境的密钥等同于没有备份**。重启依赖"从活进程提取"的步骤必须在同一
  个 shell 会话内完成提取+启动，且应先把 key 落盘到 `.env` 再动进程。多代残留进程
  要在操作前先盘点（ps + lsof），不要假定只有一个实例。
- VAD 是转录静默失败的高发点：任何"VAD 过滤后为空"都应触发无 VAD 重试，而不是直接
  判空失败。

## 回滚操作

1. 停止后端：`lsof -ti :8000 -sTCP:LISTEN | xargs kill`。
2. 数据与配置：`.env` 中的新 master key 已生效并与 `data/v2/` 绑定，回滚代码时
   **保留 `.env` 与数据不动**；被删除的旧 provider profile 本就不可解密，无需恢复。
3. Git 回滚（确认工作区无其他未保存改动后）：

   ```bash
   git checkout -- backend/v2/jobs/pipeline.py backend/v2/jobs/source_ingest.py \
     backend/v2/jobs/ai.py backend/v2/config.py backend/v2/bootstrap.py \
     backend/transcriber.py frontend/src/features/submission/SubmissionForm.tsx \
     frontend/src/features/profiles/preferences.ts \
     tests/ docs/api/v2-api-contract.md .env.example AGENTS.md
   git clean -f backend/v2/jobs/subtitles.py tests/v2/test_subtitles.py
   ```

4. 回滚后用根 venv 重启（`cd backend && set -a && source ../.env && set +a && ../venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log`），
   跑 `python -m unittest discover tests` 与 `curl --fail http://127.0.0.1:8000/api/v2/dashboard`
   验证。
