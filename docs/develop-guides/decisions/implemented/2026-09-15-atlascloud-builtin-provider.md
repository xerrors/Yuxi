# Atlas Cloud 作为内置 OpenAI 兼容供应商

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/providers/builtin.py

## 问题

Atlas Cloud 是 OpenAI 兼容的聚合网关，一个 API Key 后面挂着 100 多个开源与前沿模型。管理员目前只能在模型配置页手工新建自定义供应商，自己填 base_url、凭证环境变量和模型发现端点；这三项任何一项写错，表现都是模型列表为空或请求 401，而不是一个明确的配置错误。OpenRouter、ModelScope 等同类聚合供应商已经是内置模板，Atlas Cloud 没有理由例外。

## 决策

在 `BUILTIN_PROVIDERS` 增加一条 `atlascloud` 模板，紧邻 OpenRouter：`base_url` 为 `https://api.atlascloud.ai/v1`，`api_key_env` 为 `ATLASCLOUD_API_KEY`，`models_endpoint` 为 `https://api.atlascloud.ai/v1/models`。不声明 `capabilities`，因此只走 chat 端点；不声明 `provider_type`，由 `_normalize_payload` 归一化为 `openai`；不预置 `enabled_models`，模型由发现端点实时拉取。`is_enabled` 沿用 `ensure_builtin_providers` 的既有规则（只有 `siliconflow-cn` 默认启用），新模板默认关闭，管理员显式启用后才生效。

配套只有三处声明：`.env.template` 的注释行、`docs/intro/model-config.md` 的供应商表格行、`web/src/utils/modelIcon.js` 的头像条目（图标来自 `@lobehub/icons-static-svg` 已有的 `atlascloud`，不新增仓库内资源）。

## 替代方案

预置 `enabled_models` 会把模型清单变成需要跟随网关上下线维护的仓库内事实，发现端点已经提供同样信息且永远是最新的。声明 embedding 能力会多打一个该网关不提供的端点，把一次必然失败的请求伪装成能力。为该网关扩展 `_normalize_remote_model` 的取值路径会改动所有供应商共享的公共归一化契约，属于独立变更。把 Atlas Cloud 加进 `docs/public/home/providers/` 的首页供应商墙是市场展示而非接入能力，不在本变更范围。

## 后果

模板新增后，`ensure_builtin_providers` 会在下次启动时为存量部署补出一条默认关闭的 `atlascloud` 供应商记录；已由管理员编辑过的同名配置不会被覆盖。未设置 `ATLASCLOUD_API_KEY` 时凭证状态为 `warning`，模型发现请求不带 `Authorization`，由网关返回 401。

已知差异：Atlas Cloud 把 `input_modalities` / `output_modalities` 放在响应顶层，而 `_normalize_remote_model` 只从 `architecture` 子对象取模态，因此这两个字段归一化后为空列表；`id`、`name`、`description`、`context_length`、`pricing` 均正常解析，原始响应完整保留在 `raw_metadata`。该差异被测试钉住，接上模态需要改公共归一化路径。

## 验证

`backend/test/unit/services/test_atlascloud_provider.py` 以 2026-09-15 从网关取回的真实 `/v1/models` 条目为 oracle，覆盖模板三要素、`provider_type` 归一化、凭证状态随环境变量变化（含缺失时为 `warning` 的负向用例）、发现请求命中真实端点并携带 Bearer、缺 Key 时不得凭空生成 `Authorization`、未声明 embedding 能力时只打一次端点、响应非列表时必须抛错而不是静默返回空，以及模态字段当前为空的已知差异。

真实连通性由一次针对生产网关的手工探针证明：按本模板构造 provider 后 `check_credential_status` 为 `ok`，`fetch_remote_models` 返回 118 个模型，`type` 全部为 `chat`，`context_length` 与 `pricing.prompt` 均有值。该探针依赖外部服务，不进确定性用例。

未验证范围：没有跑通该供应商下的真实对话链路（需要完整 Docker 拓扑与数据库），也没有在真实浏览器里确认模型配置页的头像渲染。
