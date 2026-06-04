import re

files = [
    "backend/test/unit/services/test_mcp_connection_service.py",
    "backend/test/unit/services/test_mcp_service.py",
    "backend/test/unit/services/test_mcp_service_auth_runtime.py"
]

replacements = {
    "create_mcp_connection": "connection_service",
    "list_mcp_connections": "connection_service",
    "set_mcp_connection_status": "connection_service",
    "delete_mcp_connection": "connection_service",
    "reauthorize_mcp_connection": "connection_service",
    "update_mcp_connection": "connection_service",
    "test_mcp_connection": "connection_service",
    "get_mcp_connection": "connection_service",
    "_resolve_scope_id": "connection_service",
    
    "get_mcp_server_dependency_summary": "server_service",
    "set_server_enabled": "server_service",
    "get_runtime_mcp_server_config": "server_service",
    "get_enabled_mcp_server_config": "server_service",
    "_load_enabled_mcp_server_configs": "server_service",
    
    "get_mcp_tools": "tool_registry_service",
    "get_enabled_mcp_tools": "tool_registry_service",
    "get_all_mcp_tools": "tool_registry_service",
    "get_tools_from_all_servers": "tool_registry_service",
    "clear_mcp_cache": "tool_registry_service",
    "_mcp_tool_cache_store": "tool_registry_service",
    "_clear_mcp_server_runtime_auth_cache": "tool_registry_service",
}

for file_path in files:
    with open(file_path, "r") as f:
        content = f.read()

    # 替换 import
    content = content.replace("from yuxi.services import mcp_service", "from yuxi.services.mcp import connection_service, server_service, tool_registry_service\nfrom yuxi.services.mcp.client_pool import mcp_client_pool\nfrom yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache")

    # 特殊替换 get_mcp_client
    content = content.replace("mcp_service.get_mcp_client", "mcp_client_pool._get_mcp_client")
    content = content.replace('mcp_service", "get_mcp_client"', 'mcp_client_pool", "_get_mcp_client"')

    # 替换 mcp_service.func -> specific_service.func
    for func, service in replacements.items():
        content = re.sub(rf'mcp_service\.{func}', f'{service}.{func}', content)
        content = re.sub(rf'mcp_service",\s*"{func}"', f'{service}", "{func}"', content)
        
    # 对于剩余的 mcp_service，如果是 monkeypatch.setattr(mcp_service, "RedisTokenCache"
    content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"RedisTokenCache"', 'monkeypatch.setattr(connection_service, "RedisTokenCache"', content)

    with open(file_path, "w") as f:
        f.write(content)
