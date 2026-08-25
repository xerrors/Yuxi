from fastapi import APIRouter, Depends, HTTPException, Query

from yuxi.storage.postgres.models_business import User
from yuxi.services.task_service import tasker
from server.utils.auth_middleware import get_admin_user

tasks = APIRouter(prefix="/tasks", tags=["tasks"])


@tasks.get("")
async def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(get_admin_user),
):
    """List tasks, optionally filtered by status."""
    return await tasker.list_tasks(status=status, limit=limit)


@tasks.get("/{task_id}")
async def get_task(task_id: str, current_user: User = Depends(get_admin_user)):
    """Retrieve a single task by id."""
    task = await tasker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task}


@tasks.post("/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: User = Depends(get_admin_user)):
    """Request cancellation of a task."""
    task = await tasker.cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")
    return {
        "task_id": task_id,
        "status": task.status,
        "cancel_requested": task.cancel_requested,
    }


@tasks.delete("/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends(get_admin_user)):
    """Delete a task by id."""
    success = await tasker.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=409, detail="Task must exist and be terminal before deletion")
    return {"task_id": task_id, "status": "deleted"}
