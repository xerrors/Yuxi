# 配置模型

Yuxi 在“智能体 → 模型供应商”中统一管理聊天、嵌入和重排模型。只有管理员可以新增或修改供应商；普通用户可以在有权限的地方选择已经启用的模型。

## 配置顺序

1. 打开“智能体 → 模型供应商”。
2. 新增供应商，或打开一个内置供应商。
3. 填写 API 地址和凭证，选择供应商能力。
4. 在供应商的“模型配置”中获取远程模型，或手动添加模型。
5. 对模型执行连接测试，再把它选为智能体或知识库使用的模型。

供应商停用后，其模型不会进入运行时模型缓存。Web 管理页面会在系统默认模型仍引用某个供应商或模型时阻止删除或停用，先切换默认模型再修改；直接调用管理 API 时也应先检查并替换默认引用，不能依赖页面保护。

## 凭证怎么保存

供应商支持两种凭证来源：

| 来源 | 配置位置 | 适用场景 |
| --- | --- | --- |
| 环境变量 | 供应商的“API Key Env”填写变量名，密钥放在 API/worker 环境中 | 生产环境，推荐 |
| 直接填写 | 供应商的“API Key”字段 | 本地测试或明确接受数据库保存凭证的环境 |

使用环境变量时，字段里填写变量名，例如 `SILICONFLOW_API_KEY`，不要把密钥本身写进文档。修改容器环境变量后需要重新创建读取它的 API 和 worker；供应商页面的保存不会替你更新容器：

```bash
docker compose up -d --force-recreate api worker
```

## 内置供应商

系统启动时会同步内置供应商模板。模板提供供应商 ID、API 地址、凭证变量名和模型发现地址；是否可用取决于凭证、供应商状态和已启用模型。页面列出的内容是当前实例的实际配置，完整模板由 [`builtin.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/models/providers/builtin.py) 维护。

内置供应商模板的完整映射如下。表中类型是模板预置或当前常见用途；模型仍需在供应商中启用，实际可用性以当前实例配置和供应商接口为准。

| 展示名称 | Provider ID | 常见类型 | 凭证环境变量 |
| --- | --- | --- | --- |
| OpenAI | `openai` | chat | `OPENAI_API_KEY` |
| DeepSeek | `deepseek` | chat | `DEEPSEEK_API_KEY` |
| DashScope（中国站） | `alibaba-cn` | chat、embedding、rerank | `DASHSCOPE_API_KEY` |
| DashScope（国际站） | `alibaba` | chat | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan（中国站） | `alibaba-coding-plan-cn` | chat | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan（国际站） | `alibaba-coding-plan` | chat | `DASHSCOPE_API_KEY` |
| Zhipu（BigModel） | `zhipuai` | chat | `ZHIPUAI_API_KEY` |
| Zhipu Coding Plan（BigModel） | `zhipuai-coding-plan` | chat | `ZHIPUAI_API_KEY` |
| Zhipu（Z.AI） | `zai` | chat | `ZAI_API_KEY` |
| Zhipu Coding Plan（Z.AI） | `zai-coding-plan` | chat | `ZAI_API_KEY` |
| XiaomiMiMo Token Plan | `xiaomi-token-plan-cn` | chat | `XIAOMI_MIMO_TOKEN_PLAN_API_KEY` |
| XiaomiMiMo | `xiaomi` | chat | `XIAOMI_MIMO_API_KEY` |
| Kimi Code | `kimi-for-coding` | chat | `KIMI_CODE_API_KEY` |
| Moonshot（中国站） | `moonshotai-cn` | chat | `MOONSHOT_API_KEY` |
| Moonshot（国际站） | `moonshotai` | chat | `MOONSHOT_API_KEY` |
| MiniMax（中国站） | `minimax-cn` | chat | `MINIMAX_API_KEY` |
| MiniMax（国际站） | `minimax` | chat | `MINIMAX_API_KEY` |
| OpenRouter | `openrouter` | chat、embedding | `OPENROUTER_API_KEY` |
| ModelScope | `modelscope` | chat | `MODELSCOPE_ACCESS_TOKEN` |
| OpenCode | `opencode` | chat | 无默认环境变量 |
| OpenCode Go | `opencode-go` | chat | 无默认环境变量 |
| SiliconFlow（中国站） | `siliconflow-cn` | chat、embedding、rerank | `SILICONFLOW_API_KEY` |
| SiliconFlow（国际站） | `siliconflow` | chat、embedding、rerank | `SILICONFLOW_GLOBAL_API_KEY` |

其中 `alibaba-cn`、`openrouter`、`siliconflow-cn` 和 `siliconflow` 的模板明确包含嵌入或重排能力；其他供应商是否能添加某类模型，取决于当前供应商配置的能力和接口。不要把 `alibaba` 和 `alibaba-cn` 混用：前者是 DashScope 国际站模板，后者带有内置的嵌入和重排配置。

## 添加和启用模型

### 从远程列表添加

打开供应商的模型配置，点击“获取远程模型”，从返回列表中选择模型。远程列表只用于发现候选项，不会自动启用模型；确认添加后，模型才会进入运行时。

### 手动添加

点击“手动添加”，填写模型 ID 和类型：

- `chat`：聊天和智能体运行。
- `embedding`：知识库向量化，需要填写供应商规格中的向量维度。
- `rerank`：对检索候选结果重排。

知识库创建后，嵌入模型和向量维度属于索引的一部分。更换嵌入模型或维度后，需要按知识库流程重建索引，不能把不同向量空间的结果混在一起。

### 模型标识

运行时使用 `provider_id:model_id`，只按第一个冒号分隔供应商和模型 ID。模型 ID 可以包含斜杠，例如：

```text
siliconflow-cn:Pro/BAAI/bge-m3
```

页面中的模型选择器会显示供应商名称和模型名称；在 API、日志或配置快照中排查问题时，使用完整 spec。

## 配置聊天模型的请求参数

OpenAI Completions API 兼容供应商的 `chat` 模型可以配置“模型请求参数 JSON”。Yuxi 会把它作为 OpenAI SDK 的 `extra_body` 合并到请求体顶层，用于支持不同供应商的思考或推理参数。

当前允许的顶层字段是：

| 字段 | 常见用途 |
| --- | --- |
| `enable_thinking` | 开关式思考配置 |
| `thinking_budget` | 思考 Token 预算 |
| `thinking` | 供应商的思考配置对象 |
| `reasoning` | 推理配置对象 |
| `reasoning_effort` | OpenAI 风格的推理强度 |

示例：

```json
{
  "enable_thinking": true,
  "thinking_budget": 1024
}
```

白名单只限制顶层字段，字段内部结构和可用取值由供应商校验。参数是否生效取决于模型和供应商接口，Yuxi 不会把不支持的字段转换成另一种格式。Anthropic、Gemini 等非 OpenAI 兼容供应商不能使用这组 `extra_body` 覆盖。

## 按用户统计模型用量

在 Yuxi 之外按用户计量模型用量时，先在 API/worker 的环境变量中放置一把专用随机密钥（例如 `YUXI_UID_SIGNATURE_SECRET=$(openssl rand -hex 32)`；不要复用 `JWT_SECRET_KEY` 等认证密钥），再在供应商的编辑或新增表单中打开“请求携带用户 ID”。开启后，智能体对话产生的聊天模型请求会带上带 HMAC 签名的 `x-yuxi-uid` 请求头，值为发起对话用户的 UID；外部网关或供应商网关验签后即可把用量归属到具体用户，并阻止不知道签名密钥的 API Key 持有者任意改写 UID。时间戳只限制旧签名的重用窗口：捕获到的签名头仍可在有效窗口内重复使用，当前协议不是严格的一次性防重放机制。供应商卡片会显示当前是否开启。

该开关默认关闭，只对开启它的供应商生效。知识库抽取、评测等后台系统任务不携带该头，用量按系统 API Key 计量。开启即表示该供应商的请求会携带用户 UID，请确认供应商或中间网关接受这个额外请求头。保存时 Yuxi 会校验 `YUXI_UID_SIGNATURE_SECRET` 是否已配置，未配置则拒绝保存并提示配置方法；密钥只从这同一个固定变量读取，不写入数据库。该头只覆盖 OpenAI 兼容与 Anthropic 供应商；Gemini 不支持此请求头，Yuxi 会禁用该选项并拒绝通过管理 API 开启。

时间戳与签名按请求现算，不在加载模型时固定：长对话中每轮模型请求（包括工具调用后的下一轮、稍后触发的上下文摘要、网络重试后的重发）都会重新生成时间戳并重新签名，因此不会因为一轮任务跨越时间窗口而被网关按重放拒绝。

### 请求头与验签

Yuxi 对 `uid=<uid>\nts=<unix 时间戳>` 计算 HMAC-SHA256，随请求附加三个头：

| 请求头 | 内容 |
| --- | --- |
| `x-yuxi-uid` | 用户 UID |
| `x-yuxi-uid-ts` | 签名时的 Unix 时间戳（秒） |
| `x-yuxi-uid-sig` | `base64(HMAC-SHA256(密钥, "uid=<uid>\nts=<ts>"))` |

网关按请求使用的 API Key 查找对应密钥，校验时间戳窗口后重算比对：

```python
import base64, hashlib, hmac

def verify(secret: str, uid: str, ts: str, sig: str, *, now: int, window: int = 300) -> bool:
    try:
        issued_at = int(ts)
    except (TypeError, ValueError):
        return False
    if abs(now - issued_at) > window:  # 限制签名可重用的时间窗口
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode(), f"uid={uid}\nts={ts}".encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, sig)
```

窗口（示例为 ±300 秒）之外的请求会被拒绝。当前签名只覆盖 UID 和时间戳，没有每请求唯一 nonce；捕获到的签名头仍可在有效窗口内重复使用，因此这不是严格的一次性防重放机制。若业务要求阻止窗口内重放，网关协议还需加入每请求唯一 nonce/请求标识并原子去重。签名证明“产出方持有共享密钥且 uid 未被篡改”，不能阻止本就持有密钥的 Yuxi 管理员伪造。若保存后环境变量又被移除（例如只重建了部分容器），Yuxi 在发请求时直接报错，不静默降级为未签名头。

签名密钥只从固定的 `YUXI_UID_SIGNATURE_SECRET` 读取，provider 配置里没有任何环境变量名字段：签名无法被指向其他变量，也就不存在借签名头探测服务器有哪些环境变量、或对某个密钥做离线猜解的通道。同一 Yuxi 实例的所有已签名供应商共用这一把密钥；如果未来需要按网关各持各钥，应在服务端以白名单形式开放命名空间，而不是在 provider 配置里接受自由输入的变量名。

### Provider / 网关侧接入

负责计量的 Provider 或网关需要在自有安全配置中保存与 Yuxi API、worker 相同的 `YUXI_UID_SIGNATURE_SECRET`，并在接收请求时读取 `x-yuxi-uid`、`x-yuxi-uid-ts` 和 `x-yuxi-uid-sig`。按接入该网关的 API Key 找到对应的 Yuxi 签名密钥，使用上面的 `verify()` 校验时间戳窗口和 HMAC；只有验证成功后，才把 UID 作为已验证身份写入用量记录。

```python
import time
from fastapi import HTTPException, Request


def verified_yuxi_uid(request: Request) -> str:
    uid = request.headers.get("x-yuxi-uid", "")
    ts = request.headers.get("x-yuxi-uid-ts", "")
    sig = request.headers.get("x-yuxi-uid-sig", "")
    secret = secret_for_api_key(request.headers.get("authorization", ""))
    if not uid or not verify(secret, uid, ts, sig, now=int(time.time())):
        raise HTTPException(status_code=401, detail="invalid Yuxi user signature")
    return uid


record_usage(verified_uid=verified_yuxi_uid(request))
```

示例中的 `secret_for_api_key()` 应由网关实现：先完成 API Key 认证，再把该 Key 映射到对应 Yuxi 实例的签名密钥；单实例部署可直接读取该实例的安全配置。Provider 前的反向代理必须在移除或重写自定义请求头前完成验签。若目标 Provider 不保留或不接受这些请求头，应在它前面部署能读取并验证请求头的网关，或关闭该 Provider 的“请求携带用户 ID”开关；不能把未验签的 `x-yuxi-uid` 用作配额或计费身份。

## 移除旧模型配置

在供应商的已启用模型列表中移除模型。Web 页面不会让当前默认模型直接移除，先在系统配置中换用其他模型再操作；直接调用管理 API 时需要自行保证默认引用仍然有效。知识库的嵌入模型变更后，按知识库页面重新建立索引。

旧版 `provider/model`、旧知识库 JSON 模型字段以及配置文件中的 `model_names`、`embed_model_names`、`reranker_names` 不属于当前运行时来源。升级后如果历史 Agent 或知识库仍保存旧格式，请在界面重新选择模型并保存；知识库嵌入模型变更后还要重建索引。

## Ollama

当前版本没有 Ollama provider 类型，也没有 Ollama embedding 运行时适配。已有 Ollama embedding 知识库需要选择新的嵌入模型并重建索引。

## 排查模型不可用

按下面顺序检查：

1. 供应商是否启用，模型是否位于“已启用模型”列表。
2. API 地址是否能从 API/worker 容器访问。
3. API Key Env 对应的变量是否存在，或直接凭证是否正确。
4. 模型类型、嵌入维度和供应商能力是否匹配。
5. 在供应商详情中执行连接测试，并查看 API/worker 日志中的错误。

模型连接测试能证明该次请求得到响应，不能证明所有模型参数、工具调用或知识库索引都适配；这些行为仍需在实际 Agent 或知识库链路中验证。
