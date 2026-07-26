# Provider Model Discovery 设计

## 1. 背景与目标

VIDA v2 的 Provider Profile 表单当前要求用户手工输入 `modelId`。已有 `static/` 旧版界面会在
Base URL 和 API Key 输入停止 900ms 后获取模型，同时保留 Fetch 按钮、状态提示和模型下拉框。
v2 应复用这套成熟交互，但不能直接调用旧版 `/api/models`：该接口不支持复用 v2 加密保存的密钥，
也不满足 v2 统一错误、请求 ID 和上游错误脱敏要求。

本次目标：

- 新建 Profile 时，使用尚未保存的 Base URL 和 API Key 获取模型；
- 编辑 Profile 时，在 API Key 输入框保持空白的情况下安全复用当前 active revision 的已保存密钥；
- 自动获取模型，并允许用户从下拉框选择 `modelId`；
- 保留手动刷新、加载状态、错误反馈和竞争请求保护；
- 获取模型不创建或修改 Profile revision，不持久化草稿凭据。

## 2. 范围

### 2.1 包含

- 新增 v2 Provider 模型发现 API；
- 后端 Provider 模型列表调用、规范化、超时和错误脱敏；
- Profile 表单的自动获取、刷新按钮、状态行和模型下拉框；
- 新建与编辑两种凭据解析方式；
- API 契约、后端测试、前端测试和当日 devnote。

### 2.2 不包含

- 修改旧版 `/api/models` 或 `static/`；
- 自动保存 Profile；
- 在数据库中缓存 Provider 模型列表；
- 多 Provider 专用适配器或非 OpenAI-compatible 模型协议；
- 可自由输入的新模型 ID combobox；
- 修改 Episode 提交或 Job revision 固定规则。

## 3. 方案选择

采用独立的 v2 草稿模型发现接口。该方案既能在 Profile 创建前使用临时凭据，又能在编辑时由后端
读取加密保存的 active revision 密钥。相比直接复用旧版接口，它保持 v2 安全契约；相比先保存再
获取，它不会产生无意义 revision，也不会要求用户先填写假的模型 ID。

## 4. API 契约

新增：

```text
POST /api/v2/provider-profiles/models
```

### 4.1 请求

```json
{
  "profileId": "optional-existing-profile-id",
  "baseUrl": "https://api.example/v1",
  "apiKey": "optional-draft-api-key"
}
```

规则：

- `baseUrl` 必填，必须是合法 HTTP(S) URL；
- `apiKey` 若提供，trim 后必须非空，并作为本次请求的临时覆盖值；
- 新建模式没有 `profileId`，因此必须提供 `apiKey`；
- 编辑模式提供 `profileId`，`apiKey` 可省略；省略时使用该 Profile active revision 的已解密密钥；
- 编辑模式即使修改了 `baseUrl`，仍可搭配已保存密钥发现模型；
- 请求不保存 Base URL、API Key 或模型列表，也不创建 revision；
- 未知字段返回 v2 `validation_error`。

若 `profileId` 不存在或已删除，返回：

```json
{
  "error": {
    "code": "provider_profile_not_found",
    "message": "Provider profile not found",
    "details": {},
    "requestId": "uuid"
  }
}
```

### 4.2 成功响应

```json
{
  "models": [
    {
      "id": "model-id",
      "name": "Model name"
    }
  ],
  "latencyMs": 123
}
```

规范化规则：

- 忽略没有非空字符串 `id` 的记录；
- 按 `id` 去重，重复记录保留首次出现的有效名称；
- `name` 缺失或为空时回退为 `id`；
- 按 `id` 做稳定、大小写不敏感排序；
- 最多返回 2000 个唯一模型；
- `id` 和 `name` 最长 512 个字符，超长记录忽略，避免不受控响应和 UI option；
- 无有效模型时返回成功和空数组，由 UI 显示 “No models returned”。

### 4.3 上游失败

连接失败、鉴权失败、超时或上游响应异常统一返回 HTTP 502：

```json
{
  "error": {
    "code": "provider_models_fetch_failed",
    "message": "Unable to fetch provider models",
    "details": {},
    "requestId": "uuid"
  }
}
```

不透传上游响应 body、URL query、header、API Key、SDK 异常文本或内部 traceback。Provider 客户端
使用 15 秒总超时并在成功或失败后关闭。

## 5. 后端设计

### 5.1 Schema

在 `backend/v2/schemas.py` 新增：

- `ProviderModelDiscoveryRequest`
- `ProviderModelOptionResponse`
- `ProviderModelDiscoveryResponse`

请求模型使用 `extra="forbid"`。model validator 保证 `profileId` 与 `apiKey` 至少有一个；API Key
使用 `SecretStr`，错误和 repr 不显示明文。

### 5.2 可注入的模型发现能力

在 runtime 中新增明确的 `ProviderModelFetcher` 协议：

```python
ProviderModelFetcher = Callable[
    [str, str],
    Awaitable[tuple[list[ProviderModelOption], int]],
]
```

生产 runtime 注入 OpenAI SDK 实现；测试 runtime 注入 AsyncMock。路由不直接创建 SDK client，
便于隔离凭据解析、响应映射和错误脱敏测试。

生产 fetcher：

1. 使用请求解析出的 `baseUrl` 与 `apiKey` 创建 OpenAI client；
2. 在线程中执行 `client.models.list()`；
3. 计算 monotonic latency；
4. 在 `finally` 中关闭 client；
5. 规范化、去重、排序并限制模型列表；
6. 所有非 `V2Error` 异常在 API 边界转换为安全的
   `provider_models_fetch_failed`。

已有连接测试继续验证当前 `modelId` 是否存在；模型规范化 helper 可被连接测试和发现接口共同使用，
但不改变现有连接测试响应契约。

### 5.3 凭据解析

API 路由按以下优先级解析 API Key：

1. 请求携带的草稿 `apiKey`；
2. `profileId` 对应 active revision 的已保存密钥；
3. 两者都没有时由 schema 拒绝。

`baseUrl` 始终来自请求，使用户能在保存前测试新地址。读取已保存密钥只发生在服务端，响应和日志
不包含明文。

## 6. 前端设计

### 6.1 API 类型

在 `frontend/src/api/types.ts` 增加：

- `ProviderModelDiscoveryInput`
- `ProviderModelOption`
- `ProviderModelDiscovery`

在 `frontend/src/api/profiles.ts` 增加：

```ts
discoverModels(
  input: ProviderModelDiscoveryInput,
  signal?: AbortSignal,
): Promise<ProviderModelDiscovery>
```

### 6.2 独立 hook

新增 `frontend/src/features/profiles/useProviderModels.ts`，负责：

- 900ms debounce；
- `AbortController` 取消上一请求；
- 单调 request token，防止取消未及时生效时旧结果覆盖新结果；
- `idle | loading | success | error` 状态；
- 模型列表、latency 和安全错误文案；
- 立即执行的手动 `refresh()`；
- 表单卸载时取消请求。

自动触发条件：

- Base URL 是可解析的 HTTP(S) URL；
- 新建模式必须已有非空 API Key；
- 编辑模式有 `profileId` 即可，API Key 留空时复用保存值；
- 任一条件不足时保持 idle，不发送请求。

### 6.3 Profile 表单

参照 `static/`：

- API Key 输入框右侧放置 `Fetch models` 按钮；
- loading 时按钮禁用，并显示旋转图标或等价 loading indicator；
- API Key 行下方显示状态：
  - `Fetching models…`
  - `Loaded N models · X ms`
  - `No models returned`
  - 安全错误文案；
- `Model ID` 文本输入替换为原生 `<select>`；
- 未获取时显示 `Fetch models to choose` placeholder；
- loading 时保留当前 options，避免控件跳空；
- 新建模式必须从非空模型列表选择后才能提交；
- 编辑模式打开后自动使用保存密钥获取模型；
- 当前已保存模型不在新列表中时，额外保留：
  `Current: {modelId} — not returned by provider`；
- 模型获取成功不自动修改或保存 Profile；只有用户改变 select 并点击 Save/Create 才提交。

Base URL 或 API Key 变化时启动新的 900ms debounce。Fetch 按钮跳过 debounce 立即请求。API Key
始终保持 password input，不缓存到 localStorage，也不写入状态提示或错误。

## 7. 并发与状态一致性

每次发现请求绑定当前的 `profileId + baseUrl + apiKey presence` 快照。后续输入变化会取消旧请求并
增加 request token。只有 token 与最新值一致的响应才能更新模型列表或错误。

切换新建/编辑 Profile 时 hook 重置状态并取消原请求。一个 Profile 的返回结果不能出现在另一个
Profile 表单中。

## 8. 测试策略

### 8.1 后端

扩展 `tests/v2/test_provider_profile_api.py`：

- 新建模式使用请求 API Key；
- 编辑模式复用 active revision API Key；
- 请求 API Key 覆盖已保存值；
- 修改 Base URL 搭配已保存 API Key；
- 缺少 `profileId` 和 `apiKey` 返回 422；
- missing/deleted Profile 返回安全 404；
- fetcher 异常返回脱敏 502；
- 响应不包含 API Key、上游异常或敏感 URL。

新增或扩展 fetcher 单元测试：

- 在线程中调用 SDK；
- 15 秒 timeout；
- client 始终关闭；
- 空 ID/超长字段过滤；
- 名称回退、去重、大小写不敏感排序和 2000 条上限；
- latency 计算。

更新 `tests/v2/test_legacy_api_contract.py`，登记新公开路由；同步
`docs/api/v2-api-contract.md`。

### 8.2 前端

扩展 `frontend/src/test/settings-page.test.tsx` 或增加聚焦测试：

- 新建 Profile 输入停止 900ms 后自动请求；
- API Key 右侧 Fetch 按钮立即请求；
- 编辑 Profile 不输入 API Key 时发送 `profileId`；
- 草稿 API Key 作为覆盖值发送；
- 加载、成功、空列表和失败状态；
- 下拉选择的 modelId 随 Create/Save 提交；
- 已保存但未返回的模型仍可见；
- 新请求取消旧请求，旧响应不覆盖当前列表；
- 表单卸载取消请求。

使用 fake timers 验证 debounce，不依赖真实等待。

## 9. 文档与 Devnote

- 更新 `docs/api/v2-api-contract.md`；
- 在 `docs/devnote/2026-07-0725/provider-model-discovery.md` 记录：
  1. 开发内容；
  2. 学习与可沉淀经验；
  3. 回滚操作。

## 10. 验收标准

- 用户新建 Provider Profile 时，填写有效 Base URL 和 API Key 后 900ms 自动看到模型下拉列表；
- 用户可随时点击 Fetch models 刷新；
- 编辑已有 Profile 时无需重新输入 API Key 即可自动获取；
- 选择模型并保存后，Profile 的 `modelId` 与下拉选择一致；
- 快速修改凭据不会显示旧请求结果；
- 所有失败均使用 v2 安全错误，不暴露凭据或上游原始内容；
- 后端完整测试、前端测试、lint 和 build 通过；
- API 契约和 devnote 同步完成。
