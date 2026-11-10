"""知识域拥有的管理员配置。"""

from yuxi.modules.system.services.options import Option

knowledge_url_import_policy = Option(
    key="knowledge_url_import_policy",
    name="知识库 URL 导入",
    description="配置允许导入知识库的网页域名。",
    params={
        "fields": [
            {
                "key": "allowed_hosts",
                "label": "允许导入的域名",
                "type": "list[str]",
                "environment": "YUXI_URL_WHITELIST",
                "default": [],
                "help": "允许域名及其子域名；保存空列表会关闭 URL 导入。",
            }
        ]
    },
)
