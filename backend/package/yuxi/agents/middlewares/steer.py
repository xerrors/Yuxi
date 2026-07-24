"""主会话 Steer 安全点 Middleware。"""

from langchain.agents.middleware import AgentMiddleware, hook_config


class SteerMiddleware(AgentMiddleware):
    """在工具批次结束后的下一次模型调用前声明 Steer 安全点。"""

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):  # noqa: ARG002
        from yuxi.services.agent_request_queue_service import mark_pending_steer_ready

        run_id = getattr(runtime.context, "run_id", None)
        if not run_id or not await mark_pending_steer_ready(run_id):
            return None
        return {"jump_to": "end"}
