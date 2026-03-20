from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.utils.logging_config import logger


async def get_agent_history_view(
    *,
    agent_id: str,
    thread_id: str,
    current_user_id: str,
    db: AsyncSession,
) -> dict:
    if not agent_manager.get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(current_user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    messages = await conv_repo.get_messages_by_thread_id(thread_id)

    history: list[dict] = []
    role_type_map = {"user": "human", "assistant": "ai", "tool": "tool", "system": "system"}

    for msg in messages:
        user_feedback = None
        if msg.feedbacks:
            for feedback in msg.feedbacks:
                if feedback.user_id == str(current_user_id):
                    user_feedback = {
                        "id": feedback.id,
                        "rating": feedback.rating,
                        "reason": feedback.reason,
                        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                    }
                    break

        msg_dict = {
            "id": msg.id,
            "type": role_type_map.get(msg.role, msg.role),
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "error_type": msg.extra_metadata.get("error_type") if msg.extra_metadata else None,
            "error_message": msg.extra_metadata.get("error_message") if msg.extra_metadata else None,
            "extra_metadata": msg.extra_metadata,
            "message_type": msg.message_type,
            "image_content": msg.image_content,
            "feedback": user_feedback,
        }

        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": str(tc.id),
                    "name": tc.tool_name,
                    "function": {"name": tc.tool_name},
                    "args": tc.tool_input or {},
                    "tool_call_result": {"content": (tc.tool_output or "")} if tc.status == "success" else None,
                    "status": tc.status,
                    "error_message": tc.error_message,
                }
                for tc in msg.tool_calls
            ]

        history.append(msg_dict)

    logger.info(f"Loaded {len(history)} messages with feedback for thread {thread_id}")
    return {"history": history}
