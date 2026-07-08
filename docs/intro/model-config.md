# 模型配置

## 概述

系统统一通过 **智能体管理 → 模型供应商** 页面管理所有模型（对话模型、嵌入模型、重排模型），无需修改配置文件。

## 配置路径

```
智能体管理 → 模型供应商
```

模型供应商页签仅管理员可见。如果当前账号不是管理员，只能看到普通的智能体管理和个人设置入口。

## API 凭证配置

支持两种凭证配置方式：

| 方式 | 适用场景 |
|------|----------|
| 环境变量 | 生产环境或不愿在界面暴露 Key 的场景 |
| 直接填写 | 开发调试，追求配置便利性 |

**环境变量方式**：在供应商配置中填写变量名（如 `SILICONFLOW_API_KEY`），确保运行时环境已配置对应变量。

**直接填写方式**：在供应商配置中直接填入 API Key。

## 供应商管理

### 内置供应商模板

系统启动时会同步一组内置 provider 模板。模板只提供 Provider ID、Base URL、凭证环境变量和远端模型发现地址；实际是否可用仍取决于你是否配置凭证、启用供应商并添加模型。

| 供应商 | Provider ID | 支持类型 | 凭证环境变量 |
|--------|-------------|----------|--------------|
| OpenAI | `openai` | chat | `OPENAI_API_KEY` |
| DeepSeek | `deepseek` | chat | `DEEPSEEK_API_KEY` |
| DashScope | `alibaba` | chat, embedding, rerank | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan | `alibaba-coding-plan-cn` | chat | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan International | `alibaba-coding-plan` | chat | `DASHSCOPE_API_KEY` |
| Zhipu BigModel | `zhipuai` | chat | `ZHIPUAI_API_KEY` |
| Zhipu BigModel Coding Plan | `zhipuai-coding-plan` | chat | `ZHIPUAI_API_KEY` |
| Z.AI | `zai` | chat | `ZAI_API_KEY` |
| Z.AI Coding Plan | `zai-coding-plan` | chat | `ZAI_API_KEY` |
| XiaomiMiMo Token Plan | `xiaomi-token-plan-cn` | chat | `XIAOMI_MIMO_TOKEN_PLAN_API_KEY` |
| XiaomiMiMo | `xiaomi` | chat | `XIAOMI_MIMO_API_KEY` |
| Kimi Code | `kimi-for-coding` | chat | `KIMI_CODE_API_KEY` |
| Moonshot | `moonshotai-cn` | chat | `MOONSHOT_API_KEY` |
| Moonshot International | `moonshotai` | chat | `MOONSHOT_API_KEY` |
| MiniMax | `minimax-cn` | chat | `MINIMAX_API_KEY` |
| MiniMax International | `minimax` | chat | `MINIMAX_API_KEY` |
| OpenRouter | `openrouter` | chat, embedding | `OPENROUTER_API_KEY` |
| ModelScope | `modelscope` | chat | `MODELSCOPE_ACCESS_TOKEN` |
| OpenCode | `opencode` | chat | 无默认环境变量 |
| SiliconFlow | `siliconflow-cn` | chat, embedding, rerank | `SILICONFLOW_API_KEY` |
| SiliconFlow International | `siliconflow` | chat, embedding, rerank | `SILICONFLOW_GLOBAL_API_KEY` |

其中 `alibaba`、`siliconflow-cn` 预置了部分 embedding / rerank 模型；其他供应商通常需要进入详情页通过「获取远程模型」或「手动添加」补充模型。

### 操作流程

1. **新增供应商**：点击「新增供应商」，填写基本信息（Provider ID、Base URL 等）
2. **配置凭证**：填写 API Key 或环境变量名
3. **启用供应商**：开启供应商状态开关
4. **获取模型**：进入供应商详情，点击「获取远程模型」从 API 拉取可用模型列表

## 模型管理

### 添加模型

**方式一：从远端拉取**

进入供应商详情 → 点击「获取远程模型」→ 从候选列表中选择添加

**方式二：手动添加**

进入供应商详情 → 点击「手动添加」→ 填写模型 ID 和类型

### 配置参数

嵌入模型（embedding）需配置向量维度，请参考模型提供商的规格说明。

### 移除模型

在供应商详情的已启用模型列表中移除不需要的模型。

## 模型标识格式

运行时模型统一使用 `provider_id:model_id` 格式，例如 `siliconflow-cn:Pro/BAAI/bge-m3`。`model_id` 可以包含 `/`，系统只按第一个 `:` 区分供应商与模型 ID。

旧版 `provider/model`、旧版知识库 JSON 模型字段、配置文件中的 `model_names` / `embed_model_names` / `reranker_names` 不再作为运行时模型来源。历史知识库或 Agent 配置如果仍保存旧格式，需要在界面中重新选择新版模型后保存。

## Ollama 支持

当前版本不再内置 Ollama provider type，也不再提供 Ollama embedding 运行时适配。已有 Ollama embedding 知识库需要管理员选择新的 embedding 模型并重建索引，避免不同向量空间混用。

## 常见问题

**凭证缺失警告**：检查 API Key 是否正确配置，或确认环境变量是否已设置。

**模型配置未生效**：确认模型已添加至供应商的已启用列表中。
