# 章节生成为空修复开发记录

日期：2026-07-26

## 1. 开发内容

### 1.1 问题定位

- 现象：completed Episode 的章节列表为空，`warnings` 也为空，无任何错误提示。
- 章节生成是**管线自动阶段**（85% 进度），不是手动触发；另有 "Regenerate chapters"
  手动重新生成入口。VIDA 1.x 没有章节功能（`backend/*.py` 中无 chapter 实现），章节是
  v2 新增能力，不存在 v1 实现可对照。
- 用相同 prompt 对生产 Provider（DeepSeek）复现请求，原始响应为 `{"chapters": []}`：
  旧 prompt 只要求“返回 chapters 数组”，不给时长、标题、语言、章节密度和示例，模型
  合法地返回了空数组；而 `AIProcessor.generate_chapters` 当时把空数组当作合法结果
  提交（有专门的 `test_explicit_empty_chapters_are_valid` 测试），于是“成功但为空”。
- 顺带发现的第二个问题：验证重新生成时，前置 optimize 阶段连续 3 次在 20% 失败
  （`APITimeoutError` / `APIConnectionError`）。v2 `AIProcessor` 默认 120s 超时且
  `max_retries=0`；v1 `Summarizer` 不传 timeout 时走 SDK 默认（600s 含重试）。对
  deepseek-v4-flash 这类慢推理模型，单个 4000 字符分块的优化经常超过 120s。

### 1.2 关键实现

- `backend/v2/jobs/ai.py`
  - `generate_chapters` 新增 `title` / `language` 关键字参数；prompt 重写：明确
    startSec 规则（第一章从 0 开始、小于总时长）、章节密度（每 3–8 分钟一章）、
    标题语言与长度、**禁止返回空数组**，并给出 600 秒视频的完整 JSON 示例；user
    消息附带视频标题和时长。
  - 空 `chapters` 数组改为解析失败（`AIExecutionError("Chapter output is invalid")`）：
    主管线中表现为 chapters typed warning，重新生成时保留旧章节，不再出现“成功但
    为空”的静默结果。
  - `AIProcessor` 与 `OpenAICompletionRunner` 默认 `timeout_sec` 从 120 提升到 300，
    覆盖慢推理模型的分块优化耗时。
- `backend/v2/jobs/pipeline.py`：两处 `generate_chapters` 调用传入 `title`（主管线用
  可能被抓取标题替换后的值）和 `language=episode.summary_language`。
- 测试更新：`test_ai_processor.py` 空数组移入非法载荷、新增 prompt 内容断言；
  `test_pipeline.py` 4 处 AI fake 签名同步，原“空数组覆盖旧章节”测试改为“空数组
  保留旧章节并产生 chapters warning”。

### 1.3 影响范围与验证

- 公开 API、数据库结构、配置项无变化；行为变化：空章节从“静默成功”变为可见的
  chapters warning。
- 验证命令与结果：
  - `python -m unittest discover tests`：369 通过、1 跳过（TMPDIR 指向真实路径规避
    macOS `/var` symlink 的既有环境性问题）。
  - 生产复现：新 prompt 直连 Provider 返回 11 个结构完整的中文章节。
  - 端到端：重启后端后对 `0573101b` Episode 调用
    `POST /api/v2/episodes/{id}/chapters/regenerate`，约 120 秒后生成 9 个章节
    （开场与问题定义 / 人类审查与多智能体 / 四个关注点 / 架构组件详解 / 故障模式分析 /
    人工参与程度 / 第一性原理 / 审查四件事与现状 / 输出结构与记忆类型），Job
    completed。
- 已知限制：300s 为固定默认值，未新增配置项；极端慢 Provider 仍可能超时，后续可按
  需要加 `V2_AI_TIMEOUT_SEC`。

## 2. 学习与可沉淀经验

- “合法但为空”是最难发现的失败模式：schema 校验通过 ≠ 业务结果可用。对 LLM 输出，
  除结构校验外还要校验业务下限（至少一个章节、摘要非空等）。
- 给模型的 prompt 必须包含任务所需的全部数值上下文（时长）和反例约束（禁止空数组）；
  只描述输出格式会得到“格式正确的空答案”。
- 排查时先用相同输入直连 Provider 复现，能把“管线 bug”和“模型行为”快速分离——本次
  一次复现就确认了根因。
- v2 把 v1 组件包进 `raise_on_error + 固定短超时` 时，要对照 v1 的实际默认行为
  （SDK 600s + 重试），否则慢模型下会出现 v1 能用、v2 失败的隐性回归。

## 3. 回滚操作

- 无数据库结构变更；已生成的章节是普通数据，回滚代码不影响其存在，不需要数据修复。
- 回滚命令：`git revert <commit>`，涉及 `backend/v2/jobs/ai.py`、
  `backend/v2/jobs/pipeline.py`、`tests/v2/test_ai_processor.py`、
  `tests/v2/test_pipeline.py`。
- 回滚后验证：`python -m unittest discover tests` 全量通过；注意回滚会恢复 120s
  超时，慢 Provider 的分块优化可能再次超时。
