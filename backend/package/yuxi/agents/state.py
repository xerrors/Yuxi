"""Define the state structures for the agent."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain.agents import AgentState


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge artifact file paths while preserving order and removing duplicates."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_files(existing: dict | None, new: dict | None) -> dict:
    """Merge state file dictionaries with latest values winning."""
    if existing is None:
        return new or {}
    if new is None:
        return existing
    return existing | new


def merge_uploads(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Replace uploads with the latest normalized snapshot."""
    return list(new or existing or [])


class BaseState(AgentState):
    """Shared state fields for Yuxi agents."""

    artifacts: Annotated[list[str], merge_artifacts]
    files: Annotated[dict, merge_files]
    uploads: Annotated[list[dict], merge_uploads]


class AgentStatePayload(TypedDict):
    """Serialized agent state payload consumed by the frontend."""

    todos: list
    files: dict
    uploads: list[dict]
    artifacts: list[str]
