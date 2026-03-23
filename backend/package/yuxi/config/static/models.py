"""
默认模型配置

该文件定义了系统支持的所有默认模型配置，包括：
- 聊天模型（LLM）
- 嵌入模型（Embedding）
- 重排序模型（Reranker）
"""

from pydantic import BaseModel, Field


class ChatModelProvider(BaseModel):
    """聊天模型提供商配置"""

    name: str = Field(..., description="提供商显示名称")
    url: str = Field(..., description="提供商文档或模型列表 URL")
    base_url: str = Field(..., description="API 基础 URL")
    default: str = Field(..., description="默认模型名称")
    env: str = Field(..., description="API Key 环境变量名")
    models: list[str] = Field(default_factory=list, description="支持的模型列表")
    custom: bool = Field(default=False, description="是否为自定义供应商")


class EmbedModelInfo(BaseModel):
    """嵌入模型配置"""

    name: str = Field(..., description="模型名称")
    dimension: int = Field(..., description="向量维度")
    base_url: str = Field(..., description="API 基础 URL")
    api_key: str = Field(..., description="API Key 或环境变量名")
    model_id: str | None = Field(None, description="可选的模型 ID")
    batch_size: int = Field(40, description="批量向量化大小")


class RerankerInfo(BaseModel):
    """重排序模型配置"""

    name: str = Field(..., description="模型名称")
    base_url: str = Field(..., description="API 基础 URL")
    api_key: str = Field(..., description="API Key 或环境变量名")


# ============================================================
# 默认聊天模型配置
# ============================================================

DEFAULT_CHAT_MODEL_PROVIDERS: dict[str, ChatModelProvider] = {
    "openai": ChatModelProvider(
        name="OpenAI",
        url="https://platform.openai.com/docs/models",
        base_url="https://api.openai.com/v1",
        default="gpt-5-mini",
        env="OPENAI_API_KEY",
        models=["gpt-5.2", "gpt-5-mini", "gpt-5.2-pro"],
    ),
    "deepseek": ChatModelProvider(
        name="DeepSeek",
        url="https://platform.deepseek.com/api-docs/zh-cn/pricing",
        base_url="https://api.deepseek.com/v1",
        default="deepseek-chat",
        env="DEEPSEEK_API_KEY",
        models=["deepseek-chat", "deepseek-reasoner"],
    ),
    "zhipu": ChatModelProvider(
        name="智谱AI (Zhipu)",
        url="https://open.bigmodel.cn/dev/api",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        default="glm-4.7-flash",
        env="ZHIPUAI_API_KEY",
        models=["glm-5", "glm-4.5-air", "glm-4.7-flash"],
    ),
    "siliconflow": ChatModelProvider(
        name="SiliconFlow",
        url="https://cloud.siliconflow.cn/models",
        base_url="https://api.siliconflow.cn/v1",
        default="Pro/deepseek-ai/DeepSeek-V3.2",
        env="SILICONFLOW_API_KEY",
        models=[
            "Pro/deepseek-ai/DeepSeek-V3.2",
            "Pro/MiniMaxAI/MiniMax-M2.5",
            "Pro/zai-org/GLM-5",
            "Pro/moonshotai/Kimi-K2.5",
        ],
    ),
    # "together": ChatModelProvider(
    #     name="Together",
    #     url="https://api.together.ai/models",
    #     base_url="https://api.together.xyz/v1/",
    #     default="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
    #     env="TOGETHER_API_KEY",
    #     models=["meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"],
    # ),
    "dashscope": ChatModelProvider(
        name="阿里百炼 (DashScope)",
        url="https://bailian.console.aliyun.com/?switchAgent=10226727&productCode=p_efm#/model-market",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default="qwen-max-latest",
        env="DASHSCOPE_API_KEY",
        models=[
            "qwen-max-latest",
            "qwen-plus-latest",
            "qwen-turbo-latest",
        ],
    ),
    "ark": ChatModelProvider(
        name="豆包（Ark）",
        url="https://console.volcengine.com/ark/region:ark+cn-beijing/model",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default="doubao-seed-2-0-lite-260215",
        env="ARK_API_KEY",
        models=[
            "doubao-seed-2-0-pro-260215",
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-mini-260215",
        ],
    ),
    "minimax": ChatModelProvider(
        name="MiniMax",
        url="https://platform.minimaxi.com/document/introduction",
        base_url="https://api.minimaxi.com/v1",
        default="MiniMax-M2.7",
        env="MINIMAX_API_KEY",
        models=[
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
        ],
    ),
    "openrouter": ChatModelProvider(
        name="OpenRouter",
        url="https://openrouter.ai/models",
        base_url="https://openrouter.ai/api/v1",
        default="x-ai/grok-4.1-fast",
        env="OPENROUTER_API_KEY",
        models=[
            "anthropic/claude-opus-4.6",
            "anthropic/claude-sonnet-4.5",
            "x-ai/grok-4.1-fast",
            "x-ai/grok-4",
        ],
    ),
    # "moonshot": ChatModelProvider(
    #     name="月之暗面",
    #     url="https://platform.moonshot.cn/docs/overview",
    #     base_url="https://api.moonshot.cn/v1",
    #     default="kimi-latest",
    #     env="MOONSHOT_API_KEY",
    #     models=[
    #         "kimi-latest",
    #         "kimi-k2-thinking",
    #         "kimi-k2-0905-preview",
    #     ],
    # ), # 目前适配有问题 Error code: 400 - {'error': {'message': 'Invalid request: function name is invalid, must start with a letter and can contain letters, numbers, underscores, and dashes', 'type': 'invalid_request_error'}}   # noqa: E501
    "modelscope": ChatModelProvider(
        name="ModelScope",
        url="https://www.modelscope.cn/docs/model-service/API-Inference/intro",
        base_url="https://api-inference.modelscope.cn/v1/",
        default="deepseek-ai/DeepSeek-V3.2",
        env="MODELSCOPE_ACCESS_TOKEN",
        models=["ZhipuAI/GLM-5", "ZhipuAI/GLM-4.7-Flash", "MiniMax/MiniMax-M2.5", "moonshotai/Kimi-K2.5", ""],
    ),
}


# ============================================================
# 默认嵌入模型配置
# ============================================================

DEFAULT_EMBED_MODELS: dict[str, EmbedModelInfo] = {
    "siliconflow/BAAI/bge-m3": EmbedModelInfo(
        model_id="siliconflow/BAAI/bge-m3",
        name="BAAI/bge-m3",
        dimension=1024,
        base_url="https://api.siliconflow.cn/v1/embeddings",
        api_key="SILICONFLOW_API_KEY",
    ),
    "siliconflow/Pro/BAAI/bge-m3": EmbedModelInfo(
        model_id="siliconflow/Pro/BAAI/bge-m3",
        name="Pro/BAAI/bge-m3",
        dimension=1024,
        base_url="https://api.siliconflow.cn/v1/embeddings",
        api_key="SILICONFLOW_API_KEY",
    ),
    "siliconflow/Qwen/Qwen3-Embedding-0.6B": EmbedModelInfo(
        model_id="siliconflow/Qwen/Qwen3-Embedding-0.6B",
        name="Qwen/Qwen3-Embedding-0.6B",
        dimension=1024,
        base_url="https://api.siliconflow.cn/v1/embeddings",
        api_key="SILICONFLOW_API_KEY",
    ),
    "vllm/Qwen/Qwen3-Embedding-0.6B": EmbedModelInfo(
        model_id="vllm/Qwen/Qwen3-Embedding-0.6B",
        name="Qwen3-Embedding-0.6B",
        dimension=1024,
        base_url="http://localhost:8000/v1/embeddings",
        api_key="no_api_key",
    ),
    "ollama/nomic-embed-text": EmbedModelInfo(
        model_id="ollama/nomic-embed-text",
        name="nomic-embed-text",
        dimension=768,
        base_url="http://localhost:11434/api/embed",
        api_key="no_api_key",
    ),
    "ollama/bge-m3": EmbedModelInfo(
        model_id="ollama/bge-m3",
        name="bge-m3",
        dimension=1024,
        base_url="http://localhost:11434/api/embed",
        api_key="no_api_key",
    ),
    "dashscope/text-embedding-v4": EmbedModelInfo(
        model_id="dashscope/text-embedding-v4",
        name="text-embedding-v4",
        dimension=1024,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        api_key="DASHSCOPE_API_KEY",
        batch_size=10,
    ),
}


# ============================================================
# 默认重排序模型配置
# ============================================================

DEFAULT_RERANKERS: dict[str, RerankerInfo] = {
    "siliconflow/BAAI/bge-reranker-v2-m3": RerankerInfo(
        name="BAAI/bge-reranker-v2-m3",
        base_url="https://api.siliconflow.cn/v1/rerank",
        api_key="SILICONFLOW_API_KEY",
    ),
    "siliconflow/Pro/BAAI/bge-reranker-v2-m3": RerankerInfo(
        name="Pro/BAAI/bge-reranker-v2-m3",
        base_url="https://api.siliconflow.cn/v1/rerank",
        api_key="SILICONFLOW_API_KEY",
    ),
    "dashscope/gte-rerank-v2": RerankerInfo(
        name="gte-rerank-v2",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        api_key="DASHSCOPE_API_KEY",
    ),
    "dashscope/qwen3-rerank": RerankerInfo(
        name="qwen3-rerank",
        base_url="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        api_key="DASHSCOPE_API_KEY",
    ),
    "vllm/BAAI/bge-reranker-v2-m3": RerankerInfo(
        name="BAAI/bge-reranker-v2-m3",
        base_url="http://localhost:8000/v1/rerank",
        api_key="no_api_key",
    ),
}
