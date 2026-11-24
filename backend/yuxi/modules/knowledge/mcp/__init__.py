"""知识库拥有的 MCP 协议与内部认证。"""

KNOWLEDGE_MCP_PATH = "/api/mcp"
# 平台内部使用固定 Compose 地址；外部客户端使用部署公开域名。
KNOWLEDGE_MCP_URL = f"http://api:5050{KNOWLEDGE_MCP_PATH}"
