# 新增 OrcaRouter 内置模型供应商模板

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/providers/builtin.py

## 问题

内置供应商模板缺少 OrcaRouter 路由网关，用户无法在「模型供应商」页面直接选择 OrcaRouter，需手动填写 Base URL 与模型清单。

## 决策

在 `BUILTIN_PROVIDERS` 中新增 `orcarouter` 模板，镜像 DeepSeek 的 OpenAI 兼容接线：`base_url=https://api.orcarouter.ai/v1`、`api_key_env=ORCAROUTER_API_KEY`、`models_endpoint=/v1/models`。默认 `provider_type=openai`，走既有的 OpenAI 兼容 chat 运行时（`load_chat_model` 的默认分支），无需新增 adapter。

登记点：`builtin.py` 模板条目、`docs/intro/model-config.md` 内置供应商表、`.env.template` 可选密钥注释、unit 测试断言模板可规范化。

## 替代方案

- 新增专用 `provider_type=orcarouter`：OrcaRouter 完全兼容 OpenAI Chat Completions 协议，新增枚举值只会增加校验面（`VALID_PROVIDER_TYPES`）而无行为收益，拒绝。
- 前端 icon 注册：lobehub 图标库暂无 orca/orcarouter 图标，新增条目会引用不存在的图片，拒绝；走既有 `modelAvatars.default` 兜底。

## 后果

内置模板启动时由 `ensure_builtin_model_providers_in_db` 同步，仅当 provider 不存在时创建，不影响管理员已编辑配置。聊天能力经 OpenAI 兼容运行时支持，远端模型列表由 `/v1/models` 拉取。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| `orcarouter` 模板存在且可规范化为 openai provider_type | 模板缺字段或校验失败 | `builtin.py` + `service._normalize_payload` | `pytest backend/test/unit/services/test_model_provider_service.py -k orcarouter` | provider_type 非 openai 时测试失败 | Passed |
| 文档表与 .env 注释同步 | 文档与模板不一致 | `docs/intro/model-config.md`、`.env.template` | `git diff --check` | 表中 provider_id 与代码不符 | Inspected |
| 真实聊天调用连通 OrcaRouter | 端到端不可用 | `load_chat_model` OpenAI 兼容分支 | 真 key 走 OpenAI 兼容 Chat Completions 请求 | 非 200 响应 | Passed |
