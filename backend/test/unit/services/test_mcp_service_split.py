from yuxi.services.mcp import server_service, tool_registry_service


def test_mcp_service_split_exposes_server_and_tool_boundaries():
    assert server_service.ensure_builtin_mcp_servers_in_db
    assert server_service.create_mcp_server
    assert server_service.update_mcp_server
    assert server_service.delete_mcp_server
    assert server_service.set_server_enabled
    assert server_service.toggle_tool_enabled

    assert tool_registry_service.get_mcp_tools
    assert tool_registry_service.get_enabled_mcp_tools
    assert tool_registry_service.get_all_mcp_tools
    assert tool_registry_service.clear_mcp_cache
