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

pp_structure_v3_ocr_host_opts = Option(
    key="pp_structure_v3_ocr_host_opts",
    name="PP-Structure-V3 服务",
    description="连接自行维护的 PP-Structure-V3 服务；Yuxi 不附带 PaddleX 部署。",
    params={
        "fields": [
            {
                "key": "server_url",
                "label": "服务地址",
                "type": "url",
                "environment": "PADDLEX_URI",
                "placeholder": "https://ocr.example.com",
                "help": "填写外部服务地址，或通过 PADDLEX_URI 设置；未配置时不可用。",
            }
        ]
    },
)

paddleocr_api_opts = Option(
    key="paddleocr_api_opts",
    name="PaddleOCR API",
    description="PaddleOCR-VL 和 PP-OCRv6 共用此配置。",
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

OCR_ENGINE_OPTIONS = {
    "mineru_ocr": mineru_ocr_host_opts,
    "mineru_official": mineru_official_api_opts,
    "pp_structure_v3_ocr": pp_structure_v3_ocr_host_opts,
    "paddleocr_vl_1_6": paddleocr_api_opts,
    "paddleocr_pp_ocrv6": paddleocr_api_opts,
}
DOCUMENT_OPTIONS = tuple({option.key: option for option in OCR_ENGINE_OPTIONS.values()}.values())
