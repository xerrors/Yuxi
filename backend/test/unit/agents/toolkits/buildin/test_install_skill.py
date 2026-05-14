"""Tests for install_skill tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from yuxi.agents.toolkits.buildin.install_skill import (
    ADMIN_ROLES,
    InstallSkillInput,
    _assert_admin,
    install_skill,
)

# 获取底层函数（@tool 装饰器包装为 StructuredTool 后不可直接调用）
_install_skill_func = install_skill.func


# =============================================================================
# Tests for ADMIN_ROLES
# =============================================================================

def test_admin_roles_contains_admin():
    """ADMIN_ROLES should contain 'admin'."""
    assert "admin" in ADMIN_ROLES


def test_admin_roles_contains_superadmin():
    """ADMIN_ROLES should contain 'superadmin'."""
    assert "superadmin" in ADMIN_ROLES


def test_admin_roles_is_set():
    """ADMIN_ROLES should be a set."""
    assert isinstance(ADMIN_ROLES, set)


# =============================================================================
# Tests for InstallSkillInput schema
# =============================================================================

def test_install_skill_input_source_field():
    """InstallSkillInput should have required 'source' field with proper description."""
    schema = InstallSkillInput.model_json_schema()
    
    # source field should exist
    assert "source" in schema["properties"]
    
    # source field should be required
    assert "source" in schema["required"]
    
    # Check description contains sandbox and git format descriptions
    description = schema["properties"]["source"].get("description", "")
    assert "Sandbox" in description or "沙盒" in description
    assert "Git" in description or "owner/repo" in description


def test_install_skill_input_skill_names_field():
    """InstallSkillInput should have skill_names field that is nullable with default None."""
    schema = InstallSkillInput.model_json_schema()
    
    # skill_names field should exist
    assert "skill_names" in schema["properties"]
    
    # skill_names should NOT be required
    assert "skill_names" not in schema["required"]
    
    # skill_names should be an array type
    skill_names_schema = schema["properties"]["skill_names"]
    assert skill_names_schema.get("type") == "array" or "anyOf" in skill_names_schema


def test_install_skill_input_default_values():
    """InstallSkillInput should have correct default values."""
    # Test with only required field
    input_data = InstallSkillInput(source="/path/to/skill")
    assert input_data.source == "/path/to/skill"
    assert input_data.skill_names is None


def test_install_skill_input_with_skill_names():
    """InstallSkillInput should accept skill_names list."""
    input_data = InstallSkillInput(source="owner/repo", skill_names=["skill1", "skill2"])
    assert input_data.source == "owner/repo"
    assert input_data.skill_names == ["skill1", "skill2"]


# =============================================================================
# Tests for _assert_admin
# =============================================================================

class MockUser:
    """Mock user object for testing."""
    def __init__(self, role="user", user_id="test-user"):
        self.role = role
        self.user_id = user_id


@pytest.mark.asyncio
async def test_assert_admin_missing_user_raises_error():
    """_assert_admin should raise ValueError when user does not exist."""
    mock_session = MagicMock()
    mock_user = None  # User not found
    
    with patch("yuxi.agents.toolkits.buildin.install_skill.pg_manager") as mock_pg:
        mock_pg.get_async_session_context.return_value.__aenter__.return_value = mock_session
        
        with patch("yuxi.agents.toolkits.buildin.install_skill.UserRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_by_user_id = AsyncMock(return_value=mock_user)
            mock_repo_cls.return_value = mock_repo
            
            with pytest.raises(ValueError, match="用户不存在"):
                await _assert_admin("non-existent-user")


@pytest.mark.asyncio
async def test_assert_admin_non_admin_raises_error():
    """_assert_admin should raise ValueError when user is not an admin."""
    mock_session = MagicMock()
    mock_user = MockUser(role="user", user_id="test-user")
    
    with patch("yuxi.agents.toolkits.buildin.install_skill.pg_manager") as mock_pg:
        mock_pg.get_async_session_context.return_value.__aenter__.return_value = mock_session
        
        with patch("yuxi.agents.toolkits.buildin.install_skill.UserRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_by_user_id = AsyncMock(return_value=mock_user)
            mock_repo_cls.return_value = mock_repo
            
            with pytest.raises(ValueError, match="仅管理员可以安装 skill"):
                await _assert_admin("test-user")


@pytest.mark.asyncio
async def test_assert_admin_admin_passes():
    """_assert_admin should NOT raise for admin users."""
    mock_session = MagicMock()
    mock_user = MockUser(role="admin", user_id="test-admin-user")
    
    with patch("yuxi.agents.toolkits.buildin.install_skill.pg_manager") as mock_pg:
        mock_pg.get_async_session_context.return_value.__aenter__.return_value = mock_session
        
        with patch("yuxi.agents.toolkits.buildin.install_skill.UserRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_by_user_id = AsyncMock(return_value=mock_user)
            mock_repo_cls.return_value = mock_repo
            
            # Should not raise
            await _assert_admin("test-admin-user")


@pytest.mark.asyncio
async def test_assert_admin_superadmin_passes():
    """_assert_admin should NOT raise for superadmin users."""
    mock_session = MagicMock()
    mock_user = MockUser(role="superadmin", user_id="test-superadmin-user")
    
    with patch("yuxi.agents.toolkits.buildin.install_skill.pg_manager") as mock_pg:
        mock_pg.get_async_session_context.return_value.__aenter__.return_value = mock_session
        
        with patch("yuxi.agents.toolkits.buildin.install_skill.UserRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_by_user_id = AsyncMock(return_value=mock_user)
            mock_repo_cls.return_value = mock_repo
            
            # Should not raise
            await _assert_admin("test-superadmin-user")


# =============================================================================
# Tests for install_skill function (使用 .func 访问底层函数)
# =============================================================================

def test_install_skill_no_thread_id_returns_error():
    """install_skill should return error Command when thread_id is missing."""
    runtime = MagicMock()
    runtime.context.thread_id = None
    runtime.context.user_id = "test-user"
    
    result = _install_skill_func(
        source="/home/gem/user-data/workspace/test",
        runtime=runtime,
        tool_call_id="test-call-id",
    )
    
    assert isinstance(result, Command)
    assert "messages" in result.update


def test_install_skill_no_user_id_returns_error():
    """install_skill should return error Command when user_id is missing."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = None
    
    result = _install_skill_func(
        source="/home/gem/user-data/workspace/test",
        runtime=runtime,
        tool_call_id="test-call-id",
    )
    
    assert isinstance(result, Command)
    assert "messages" in result.update


def test_install_skill_no_context_returns_error():
    """install_skill should return error Command when runtime.context is missing attributes."""
    runtime = MagicMock()
    runtime.context = SimpleNamespace()
    # No thread_id or user_id attributes
    
    result = _install_skill_func(
        source="/home/gem/user-data/workspace/test",
        runtime=runtime,
        tool_call_id="test-call-id",
    )
    
    assert isinstance(result, Command)
    assert "messages" in result.update


def test_install_skill_git_no_skill_names_returns_error():
    """install_skill should return error Command when using Git source without skill_names."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        # Mock _assert_admin to not raise (simulate admin user)
        mock_assert.return_value = None
        
        result = _install_skill_func(
            source="owner/repo",  # Git format, not starting with "/"
            skill_names=None,    # Missing skill_names
            runtime=runtime,
            tool_call_id="test-call-id",
        )
    
    assert isinstance(result, Command)
    assert "messages" in result.update
    # Check that error mentions skill_names
    error_content = str(result.update)
    assert "skill_names" in error_content or "Git" in error_content


def test_install_skill_git_with_skill_names_passes_admin_check():
    """install_skill with Git source and skill_names should pass admin check (but may fail other steps)."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    runtime.context.skills = None
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        # Mock _assert_admin to not raise
        mock_assert.return_value = None
        
        with patch("yuxi.agents.toolkits.buildin.install_skill._install_git_skills") as mock_install:
            # Mock the git installation to return success
            mock_install.return_value = [{"slug": "test-skill", "success": True}]
            
            with patch("yuxi.agents.toolkits.buildin.install_skill._enable_skill_in_current_config") as mock_enable:
                # Mock enabling skill in config
                mock_enable.return_value = True
                
                with patch("yuxi.services.skill_service.sync_thread_visible_skills"):
                    result = _install_skill_func(
                        source="owner/repo",
                        skill_names=["test-skill"],
                        runtime=runtime,
                        tool_call_id="test-call-id",
                    )
    
    assert isinstance(result, Command)
    result_data = result.update
    assert "messages" in result_data or "activated_skills" in result_data


def test_install_skill_sandbox_success():
    """install_skill with valid sandbox path should work (full mock)."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    runtime.context.skills = None
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        mock_assert.return_value = None
        
        with patch("yuxi.agents.toolkits.buildin.install_skill._install_skill_from_sandbox") as mock_install:
            mock_install.return_value = "my-skill"
            
            with patch("yuxi.agents.toolkits.buildin.install_skill._enable_skill_in_current_config") as mock_enable:
                mock_enable.return_value = True
                
                with patch("yuxi.services.skill_service.sync_thread_visible_skills"):
                    result = _install_skill_func(
                        source="/home/gem/user-data/workspace/my-skill",
                        runtime=runtime,
                        tool_call_id="test-call-id",
                    )
    
    assert isinstance(result, Command)
    assert "activated_skills" in result.update
    assert "my-skill" in result.update["activated_skills"]


def test_install_skill_value_error_handling():
    """install_skill should handle ValueError from admin check gracefully."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        # Simulate admin check failure
        mock_assert.side_effect = ValueError("仅管理员可以安装 skill")
        
        result = _install_skill_func(
            source="/home/gem/user-data/workspace/test",
            runtime=runtime,
            tool_call_id="test-call-id",
        )
    
    assert isinstance(result, Command)
    assert "messages" in result.update
    # Error message should contain the ValueError message
    result_str = str(result.update)
    assert "仅管理员可以安装 skill" in result_str


def test_install_skill_exception_handling():
    """install_skill should handle unexpected exceptions gracefully."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        # Simulate unexpected exception
        mock_assert.side_effect = RuntimeError("Unexpected error")
        
        result = _install_skill_func(
            source="/home/gem/user-data/workspace/test",
            runtime=runtime,
            tool_call_id="test-call-id",
        )
    
    assert isinstance(result, Command)
    assert "messages" in result.update
    # Error message should indicate an exception occurred
    result_str = str(result.update)
    assert "安装异常" in result_str


def test_install_skill_partial_config_failure():
    """install_skill should handle partial config persistence failure."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    runtime.context.skills = None
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        mock_assert.return_value = None
        
        with patch("yuxi.agents.toolkits.buildin.install_skill._install_skill_from_sandbox") as mock_install:
            mock_install.return_value = "my-skill"
            
            with patch("yuxi.agents.toolkits.buildin.install_skill._enable_skill_in_current_config") as mock_enable:
                # Simulate config persistence failure
                mock_enable.return_value = False
                
                with patch("yuxi.services.skill_service.sync_thread_visible_skills"):
                    result = _install_skill_func(
                        source="/home/gem/user-data/workspace/my-skill",
                        runtime=runtime,
                        tool_call_id="test-call-id",
                    )
    
    assert isinstance(result, Command)
    result_str = str(result.update)
    # Should indicate config persistence issue
    assert "持久化" in result_str or "Skill 已安装" in result_str


def test_install_skill_slug_warning_for_renamed():
    """install_skill should include warning when skill is renamed."""
    runtime = MagicMock()
    runtime.context.thread_id = "test-thread-id"
    runtime.context.user_id = "test-user"
    runtime.context.skills = None
    
    with patch("yuxi.agents.toolkits.buildin.install_skill._assert_admin") as mock_assert:
        mock_assert.return_value = None
        
        with patch("yuxi.agents.toolkits.buildin.install_skill._install_skill_from_sandbox") as mock_install:
            # Simulate skill being renamed during installation
            mock_install.return_value = "my-skill-v2"
            
            with patch("yuxi.agents.toolkits.buildin.install_skill._enable_skill_in_current_config") as mock_enable:
                mock_enable.return_value = True
                
                with patch("yuxi.services.skill_service.sync_thread_visible_skills"):
                    result = _install_skill_func(
                        source="/home/gem/user-data/workspace/my-skill",
                        runtime=runtime,
                        tool_call_id="test-call-id",
                    )
    
    assert isinstance(result, Command)
    result_str = str(result.update)
    # Should warn about slug rename
    assert "已安装为" in result_str or "⚠️" in result_str
