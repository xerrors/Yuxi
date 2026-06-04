from __future__ import annotations

from yuxi.agents.context import BaseContext


def test_base_context_accepts_internal_identity_fields_without_exposing_them_as_configurable():
    context = BaseContext()

    context.update({"department_id": "dept-9", "work_id": "login-1001"})

    assert context.department_id == "dept-9"
    assert context.work_id == "login-1001"
    configurable_items = BaseContext.get_configurable_items()
    assert "department_id" not in configurable_items
    assert "work_id" not in configurable_items
