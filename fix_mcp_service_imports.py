import re
import os

files_to_fix = [
    ("backend/package/yuxi/services/mcp_auth/orchestrator.py", "from yuxi.services.mcp_service import RedisTokenCache", "from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache"),
    ("backend/package/yuxi/services/skill_service.py", "from yuxi.services.mcp_service import get_enabled_mcp_server_names", "from yuxi.services.mcp.server_service import get_enabled_mcp_server_names"),
    ("backend/package/yuxi/agents/middlewares/dynamic_tool_middleware.py", "from yuxi.services.mcp_service import get_mcp_tools", "from yuxi.services.mcp.tool_registry_service import get_mcp_tools"),
    ("backend/package/yuxi/agents/__init__.py", "from yuxi.services.mcp_service import get_enabled_mcp_tools", "from yuxi.services.mcp.tool_registry_service import get_enabled_mcp_tools"),
    ("backend/package/yuxi/agents/middlewares/skills_middleware.py", "from yuxi.services.mcp_service import get_enabled_mcp_tools", "from yuxi.services.mcp.tool_registry_service import get_enabled_mcp_tools"),
    ("backend/package/yuxi/agents/middlewares/runtime_config_middleware.py", "from yuxi.services.mcp_service import get_enabled_mcp_tools", "from yuxi.services.mcp.tool_registry_service import get_enabled_mcp_tools"),
    ("backend/package/yuxi/agents/buildin/deep_agent/graph.py", "from yuxi.services.mcp_service import get_tools_from_all_servers", "from yuxi.services.mcp.tool_registry_service import get_tools_from_all_servers"),
    ("backend/package/yuxi/agents/buildin/chatbot/graph.py", "from yuxi.services.mcp_service import get_tools_from_all_servers", "from yuxi.services.mcp.tool_registry_service import get_tools_from_all_servers"),
    ("backend/server/utils/lifespan.py", "from yuxi.services.mcp_service import ensure_builtin_mcp_servers_in_db", "from yuxi.services.mcp.server_service import ensure_builtin_mcp_servers_in_db"),
]

for file_path, old_str, new_str in files_to_fix:
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            content = f.read()
        content = content.replace(old_str, new_str)
        with open(file_path, "w") as f:
            f.write(content)

# Fix tests
tests = [
    "backend/test/unit/services/test_mcp_auth_runtime.py",
    "backend/test/unit/services/test_mcp_tool_registry_service.py",
    "backend/test/unit/services/test_mcp_connection_service.py"
]

for file_path in tests:
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            content = f.read()
            
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"get_mcp_tools"', 'monkeypatch.setattr(tool_registry_service, "get_mcp_tools"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"get_enabled_mcp_server_config"', 'monkeypatch.setattr(server_service, "get_enabled_mcp_server_config"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"_load_enabled_mcp_server_configs"', 'monkeypatch.setattr(server_service, "_load_enabled_mcp_server_configs"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"get_runtime_mcp_server_config"', 'monkeypatch.setattr(server_service, "get_runtime_mcp_server_config"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"get_mcp_client"', 'monkeypatch.setattr(mcp_client_pool, "_get_mcp_client"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"_clear_mcp_server_runtime_auth_cache"', 'monkeypatch.setattr(tool_registry_service, "_clear_mcp_server_runtime_auth_cache"', content)
        content = re.sub(r'monkeypatch\.setattr\(mcp_service,\s*"invalidate_mcp_server_tools_cache"', 'monkeypatch.setattr(tool_registry_service, "invalidate_mcp_server_tools_cache"', content)
        
        # for `monkeypatch.setattr(\n mcp_service,\n ...)`
        content = re.sub(r'monkeypatch\.setattr\(\s*mcp_service,\s*"_mcp_tool_cache_store"', 'monkeypatch.setattr(tool_registry_service, "_mcp_tool_cache_store"', content)
        content = content.replace("await mcp_service.update_mcp_server", "await server_service.update_mcp_server")
        
        with open(file_path, "w") as f:
            f.write(content)
