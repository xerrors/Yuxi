from __future__ import annotations

from yuxi.agents.context import BaseContext


def test_base_context_accepts_department_id_without_exposing_it_as_configurable():
    context = BaseContext()

    context.update({"department_id": "dept-9"})

    assert context.department_id == "dept-9"
    assert "department_id" not in BaseContext.get_configurable_items()
