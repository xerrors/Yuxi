from __future__ import annotations

from typing import Any

from yuxi import knowledge_base


async def resolve_visible_knowledge_bases_for_context(context) -> list[dict[str, Any]]:
    user_id = getattr(context, "user_id", None)
    if not user_id:
        setattr(context, "_visible_knowledge_bases", [])
        return []

    try:
        raw_user_id = int(user_id)
    except (TypeError, ValueError):
        setattr(context, "_visible_knowledge_bases", [])
        return []

    result = await knowledge_base.get_databases_by_raw_id(raw_user_id)
    databases = result.get("databases") or []
    enabled_knowledges = getattr(context, "knowledges", None)
    if enabled_knowledges is not None:
        enabled_names = {str(name).strip() for name in enabled_knowledges if str(name).strip()}
        databases = [db for db in databases if str(db.get("name") or "").strip() in enabled_names]

    setattr(context, "_visible_knowledge_bases", databases)
    return databases
