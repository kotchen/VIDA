# Provider 模型自动发现开发记录

日期：2026-07-25  
关联设计：`docs/superpowers/specs/2026-07-25-provider-model-discovery-design.md`  
关联计划：`docs/superpowers/plans/2026-07-25-provider-model-discovery.md`

## 1. 开发内容

### 1.1 后端模型发现能力

- 新增 `POST /api/v2/provider-profiles/models`，支持使用表单中的临时 `baseUrl` 与 `apiKey`
  获取 OpenAI-compatible Provider 的模型列表。
- 编辑已有 Provider Profile 时可只传 `profileId` 和 `baseUrl`，后端使用当前激活 revision 中
  已加密保存的凭据；如果同时传入临时 API Key，则临时值优先。
- Provider 客户端设置 15 秒超时并确保请求结束后关闭；响应会过滤空白、非法和超长模型 ID，
  按 ID 去重、大小写不敏感排序，并限制最多返回 2000 项。
- 上游异常统一转换为不泄露 API Key、Provider 响应正文或内部堆栈的
  `provider_models_fetch_failed` 错误。
- API schema、公开契约和 legacy contract allowlist 已同步更新。

### 1.2 前端模型选择交互

- Settings 的 Provider 表单在 `baseUrl` 与凭据满足条件后等待 900 ms 自动获取模型；用户也可以
  点击 `Fetch models` 立即获取。
- 自动请求使用 `AbortController` 和单调 request token，表单快速变化时会取消旧请求，并忽略
  已经落后的响应。
- Model ID 从自由文本输入改为下拉框，显示 Provider 返回的模型名称和 ID；未选择模型时禁止创建
  或保存。
- 编辑已有 Profile 时不要求重新输入 API Key，并保留当前 Model ID。若 Provider 新列表不再返回
  当前模型，下拉框显示 `Current: … — not returned by provider`，避免静默清空已有配置。
- 加载、成功、空结果和失败均有可访问状态提示；重新拉取期间保留上一次模型列表和当前选择。

### 1.3 测试与验证

- 后端覆盖模型规范化、去重/排序/上限、客户端生命周期、草稿凭据、已保存凭据、临时凭据覆盖、
  validation、已删除 Profile 和错误脱敏。
- 前端覆盖 900 ms debounce、手动立即获取、加载禁用、编辑空 Key、过期响应、空结果、失败、
  Profile 切换、下拉提交和缺失当前模型保留。
- 最终验证结果：
  - 后端：346 个测试通过，1 个跳过；
  - 前端：21 个测试文件、76 个测试通过；
  - 前端 lint：通过，保留 2 条既有 Fast Refresh warning；
  - 前端生产构建：通过。
- 已知限制：自动化测试使用受控的 OpenAI-compatible mock，没有使用真实第三方 Provider 凭据
  进行联网验证；不同 Provider 对 `/models` 的兼容程度仍取决于其实现。

## 2. 学习与可沉淀经验

### 2.1 草稿配置与持久配置应共用一个受控服务端入口

新建 Profile 必须用尚未保存的 API Key 拉取模型，编辑 Profile 又应复用后端保存的密钥。让浏览器
只提交草稿字段或 Profile ID，并由后端统一解析最终凭据，可以避免把已保存的明文密钥重新发送给
前端，也让超时、错误脱敏和响应规范化只有一个实现位置。

### 2.2 Debounce 不能替代请求竞争控制

900 ms debounce 只能减少请求数量，不能防止较慢的旧请求覆盖新输入的结果。可靠实现需要同时具备
取消旧请求和 request token 校验；即使上游或 mock 忽略 abort，token 仍能阻止过期结果写入状态。

### 2.3 外部枚举结果不应破坏已保存选择

Provider 的模型列表可能因权限、区域、兼容实现或临时故障而缩减。编辑表单应把当前 Model ID
作为显式 fallback option，而不是把“本次没有返回”误判为“配置无效”并清空它。用户仍可看到差异
并主动选择新模型。

### 2.4 外部列表必须在边界处规范化

第三方响应可能包含重复、空白、非字符串或异常长 ID。后端在返回给 UI 前统一清洗、排序并设置数量
上限，可以简化前端状态，同时控制异常响应带来的内存和渲染成本。

## 3. 回滚操作

### 3.1 回滚前准备

1. 停止 Vite 和 FastAPI，避免回滚过程中继续修改 Provider Profile。
2. 备份完整 `data/v2/` 和与之匹配的 `VIDA_PROFILE_MASTER_KEY`；不要在 devnote、终端历史或
   Git 中记录密钥。
3. 确认工作区状态并记录当前版本：

   ```bash
   git status --short
   git rev-parse HEAD
   ```

### 3.2 回滚功能代码

本次功能提交从 `52f27af` 到 `543d6e1`。在已经共享的分支上使用反向提交，不重写历史：

```bash
git switch main
git pull --ff-only
git revert --no-commit 52f27af^..543d6e1
git commit -m "revert: remove provider model discovery"
```

该范围会同时移除后端发现端点、API 契约、前端 typed API、debounce hook 和 Settings 下拉交互。
保留这份 devnote 作为历史记录即可；如果设计和计划也需要撤下，可另行 revert 对应文档提交
`4aa902e` 与 `1b61e8e`。

### 3.3 数据与配置注意事项

- 本功能没有新增 SQLite table 或 migration，回滚代码通常不需要回滚数据库。
- 已经通过下拉框保存的 `modelId` 仍是既有 Profile 字段，旧版文本输入能够继续读取。
- 不要为了代码回滚删除 `data/v2/`，也不要更换已有数据对应的主密钥。
- 模型发现是只读上游调用，不会修改 Provider 侧模型或账号状态。

### 3.4 回滚后验证

```bash
source venv/bin/activate
python -m unittest discover tests
cd frontend
npm test
npm run lint
npm run build
```

启动服务后确认 Settings 恢复为目标版本的 Model ID 行为，并检查：

```bash
curl --fail http://127.0.0.1:8000/api/v2/dashboard
curl --fail http://127.0.0.1:7100/v2/
```
