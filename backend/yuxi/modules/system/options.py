"""系统配置定义；跨域汇总由 bootstrap 组合根拥有。"""

from yuxi.modules.system.services.options import Option
from yuxi.shared.web_providers import FETCH_PROVIDER_IDS, SEARCH_PROVIDER_IDS

system_options = Option(
    key="system_options",
    name="系统配置",
    description="API 与 worker 共用的管理员配置。",
    params={
        "internal": True,
        "fields": [
            {
                "key": "web_search_provider",
                "label": "默认搜索供应商",
                "type": "string",
                "environment": "WEB_SEARCH_PROVIDER",
                "default": "",
                "choices": ["", *SEARCH_PROVIDER_IDS],
            },
            {
                "key": "web_fetch_provider",
                "label": "默认网页抓取供应商",
                "type": "string",
                "default": "",
                "choices": ["", *FETCH_PROVIDER_IDS],
            },
            {
                "key": "personal_assistant_model",
                "label": "个人助理模型",
                "type": "model",
                "default": "",
            },
            {
                "key": "default_model",
                "label": "默认对话模型",
                "type": "model",
                "default": "siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
            },
            {
                "key": "fast_model",
                "label": "快速响应模型",
                "type": "model",
                "default": "siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
            },
            {
                "key": "reference_model",
                "label": "来源标注模型",
                "type": "model",
                "default": "",
            },
            {
                "key": "embed_model",
                "label": "默认 Embedding 模型",
                "type": "model",
                "default": "siliconflow-cn:Pro/BAAI/bge-m3",
            },
            {
                "key": "reranker",
                "label": "默认 Re-Ranker 模型",
                "type": "model",
                "default": "siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3",
            },
            {
                "key": "max_cooperation_depth",
                "label": "最大协作深度",
                "type": "integer",
                "min": 0,
                "default": 2,
            },
            {
                "key": "max_cooperation_concurrency",
                "label": "协作树并发执行上限",
                "type": "integer",
                "min": 1,
                "default": 4,
            },
            {
                "key": "default_ocr_engine",
                "label": "默认 OCR 解析引擎",
                "type": "ocr_engine",
                "default": "rapid_ocr",
            },
        ],
    },
)
