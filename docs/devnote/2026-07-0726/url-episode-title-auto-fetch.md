# URL Episode 标题自动抓取开发记录

日期：2026-07-26

## 1. 开发内容

### 1.1 问题定位

- 通过 URL 提交 Episode 且未填 Title 时，`EpisodeService.submit_url` 直接用 hostname 作为
  标题（例如 `www.bilibili.com`），Library 中无法辨认内容。
- yt-dlp 在下载平台页面时本来就会解析出视频标题，但 `YtDlpDownloader._download_sync`
  丢弃了 `extract_info` 的返回信息。

### 1.2 关键实现

- `backend/v2/jobs/source_ingest.py`
  - 新增 `DownloadedMedia(path, title)` 与 `MetadataDownloader` 可选协议
    （`download_with_metadata`）。
  - `YtDlpDownloader` 新增 `download_with_metadata`：复用同一次受控 SSRF 下载的
    `extract_info` 结果，标题经 `_sanitize_title` 归一化（去控制字符、折叠空白、
    上限 200 字符，与 `EpisodeTitleString` 约束一致）；原 `download()` 签名与行为不变，
    委托给新方法后只返回路径，既有调用方与安全测试不受影响。
  - `SourceIngestor.prepare` 在 downloader 实现 `MetadataDownloader` 时取回标题并写入
    `PreparedSource.source_title`（默认 `None`，上传来源恒为 `None`）。
- `backend/v2/bootstrap.py`：`_RoutingDownloader` 增加 `download_with_metadata`，平台页面
  走 yt-dlp 元数据通道，直连媒体 URL 返回 `title=None`。
- `backend/v2/jobs/pipeline.py`：仅当当前标题等于提交时的 hostname 回退值
  （`_is_fallback_title`，与 `submit_url` 的回退逻辑镜像）时，才用抓取标题替换；用户在
  表单中显式填写的标题永远不会被覆盖。**标题在来源采集完成、转录开始前立即通过
  `set_title` 落库**（约 20% 进度），并用于随后的 summary 生成；不再等到核心产物提交
  （60%）才更新。
- `backend/v2/repositories/episodes.py`：新增 `set_title(episode_id, title, updated_at)`，
  校验 1–200 字符后单事务更新；`commit_core_output` 签名保持不变。

### 1.3 影响范围与验证

- 公开 API、请求/响应字段、数据库结构、配置项均无变化，不需要 migration，API 契约不变。
- 新增测试：
  - `test_source_ingest.py`：元数据标题透传、普通 downloader 返回 `None`。
  - `test_source_ingest_security.py`：标题清洗（控制字符/空白）、缺失/非字符串/超长标题
    的容忍。
  - `test_pipeline.py`：回退标题被抓取标题替换并用于 summary；用户自定义标题不被覆盖；
    标题在转录完成前（进度 20%）即已落库。
- 验证命令与结果：
  - `python -m unittest tests.v2.test_source_ingest tests.v2.test_source_ingest_security tests.v2.test_pipeline`：76 通过。
  - `python -m unittest discover tests`：369 通过、1 跳过。
  - 已知限制：本机 macOS 的 `tempfile` 默认目录位于 `/var`（symlink 到 `/private/var`），
    会触发 SourceIngestor 既有的 attempt 路径 symlink 防护而导致 10 余个测试环境性失败
    （基线 main 上同样失败，与本次改动无关）；验证时使用 `TMPDIR=$PWD/.tmp-testtmp`
    指向真实路径后全部通过。该环境性问题建议后续单独处理。
- 运行中的后端需重启后新逻辑才生效；重启前提交的 URL Episode 仍保留 hostname 标题。
- 端到端验证（2026-07-26）：按原进程方式重启后端后，用此前完成的同一 Bilibili 链接重新提交
  且不填 Title。提交时标题为回退值 `www.bilibili.com`；第一版实现下标题在 65% 优化阶段才
  替换，体验不佳；改为采集完成后立即落库后，复验时进度 20%（Transcribing）标题已替换为
  `【GTD法】最科学的时间管理法 | 配合吃瓜实践讲解 | 解读《Getting Things Done》`，
  最终 100% completed、无 warnings。验证用 Episode `f9ff4e80-df64-411d-8d72-6233c6a52c62`
  与 `76b9fde2-95fa-4b56-86b6-4ca8ddd9cce7` 保留在库中，可在 Library 页面直接删除。

## 2. 学习与可沉淀经验

- 给既有 Protocol 增加能力时，用 `runtime_checkable` 的可选子协议
  （`MetadataDownloader`）比直接修改 `download()` 返回类型的爆炸半径小得多：
  所有 fake downloader 和安全测试无需改动。
- “是否覆盖标题”的判断依赖与 `submit_url` 回退值镜像比较，避免了新增数据库列或
  migration；代价是用户刻意把标题填成 hostname 时会被覆盖，属可接受的边缘情况。
- 外部来源（yt-dlp）的字符串一律按不受信输入处理：控制字符、超长、非字符串都要在边界
  处归一化，并与 schema 的 `EpisodeTitleString` 约束保持一致。

## 3. 回滚操作

- 无数据库结构变更、无配置变更；`data/v2/` 不需要备份或迁移。已被替换的标题只是普通
  字段值，回滚代码后不再自动更新，但已写入的标题不受影响，无需数据修复。
- 回滚命令：`git revert <commit>`，涉及文件：
  `backend/v2/jobs/source_ingest.py`、`backend/v2/jobs/pipeline.py`、
  `backend/v2/repositories/episodes.py`、`backend/v2/bootstrap.py` 及对应测试。
- 回滚后验证：`python -m unittest discover tests` 全量通过；重启后端后提交一个不带
  Title 的 Bilibili URL，确认标题恢复为 hostname 回退行为。
