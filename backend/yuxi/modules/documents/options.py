"""本模块拥有的管理员配置定义。"""

from yuxi.modules.system.services.options import Option

mineru_ocr_host_opts = Option(
    key="mineru_ocr_host_opts",
    name="MinerU 自托管",
    description="配置 MinerU 4.1 Tasks 服务，与官方云服务分别接入。",
    params={
        "fields": [
            {
                "key": "server_url",
                "label": "服务地址",
                "type": "url",
                "environment": "MINERU_API_URI",
                "placeholder": "http://mineru-api:30001",
                "help": "留空时读取 MINERU_API_URI。",
            },
            {
                "key": "tier",
                "label": "解析档位",
                "type": "string",
                "default": "standard",
                "choices": ["flash", "basic", "standard", "advanced"],
                "choice_labels": {
                    "flash": "快速（Flash）",
                    "basic": "基础（Basic）",
                    "standard": "标准（Standard）",
                    "advanced": "高精度（Advanced）",
                },
                "help": "服务必须开放所选档位。旧 Hybrid 高强度迁移为 Advanced，其余组合为 Standard；新旧质量不保证等价。",
            },
        ]
    },
)

mineru_official_api_opts = Option(
    key="mineru_official_api_opts",
    name="MinerU 官方云服务",
    description="配置 MinerU 官方云服务凭证与默认解析模型。",
    params={
        "fields": [
            {
                "key": "api_key",
                "label": "API Key",
                "type": "password",
                "environment": "MINERU_API_KEY",
                "sensitive": True,
                "help": "留空时读取 MINERU_API_KEY，建议优先使用环境变量。",
            },
            {
                "key": "model_version",
                "label": "解析模型",
                "type": "string",
                "default": "pipeline",
                "choices": ["pipeline", "vlm"],
                "choice_labels": {"pipeline": "Pipeline（传统解析）", "vlm": "VLM（视觉模型解析）"},
                "help": "Pipeline 使用传统解析流程；VLM 使用视觉模型解析。",
            },
        ]
    },
)

paddleocr_api_opts = Option(
    key="paddleocr_api_opts",
    name="PaddleOCR API",
    description="PaddleOCR-VL、PP-OCRv6 和 PP-StructureV3 共用此配置。",
    params={
        "fields": [
            {
                "key": "api_url",
                "label": "API 地址",
                "type": "url",
                "environment": "PADDLEOCR_API_URL",
                "placeholder": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
                "help": "留空时读取 PADDLEOCR_API_URL。",
            },
            {
                "key": "api_token",
                "label": "Access Token",
                "type": "password",
                "environment": "PADDLEOCR_API_TOKEN",
                "sensitive": True,
                "help": "留空时读取 PADDLEOCR_API_TOKEN，建议优先使用环境变量。",
            },
        ]
    },
)

minicpm_ocr_opts = Option(
    key="minicpm_ocr_opts",
    name="MiniCPM-OCR（自托管）",
    description="配置自托管 vLLM 的 MiniCPM-OCR 服务，逐页识别 PDF 与图片。",
    params={
        "fields": [
            {
                "key": "api_url",
                "label": "API 地址",
                "type": "url",
                "environment": "MINICPM_OCR_API_URL",
                "placeholder": "http://127.0.0.1:18499/v1/chat/completions",
                "help": "留空时读取 MINICPM_OCR_API_URL；需指向 OpenAI 兼容的 /v1/chat/completions。",
            },
            {
                "key": "api_key",
                "label": "API Key",
                "type": "password",
                "environment": "MINICPM_OCR_API_KEY",
                "sensitive": True,
                "help": "vLLM 未启用 --api-key 时可留空；留空时读取 MINICPM_OCR_API_KEY。",
            },
            {
                "key": "model",
                "label": "模型名称",
                "type": "string",
                "default": "minicpm-ocr",
                "help": "与 vLLM 的 --served-model-name 保持一致。",
            },
        ]
    },
)

OCR_ENGINE_OPTIONS = {
    "mineru_ocr": mineru_ocr_host_opts,
    "mineru_official": mineru_official_api_opts,
    "minicpm_ocr": minicpm_ocr_opts,
    "paddleocr_vl_1_6": paddleocr_api_opts,
    "paddleocr_pp_ocrv6": paddleocr_api_opts,
    "paddleocr_pp_structure_v3": paddleocr_api_opts,
}
DOCUMENT_OPTIONS = tuple({option.key: option for option in OCR_ENGINE_OPTIONS.values()}.values())
