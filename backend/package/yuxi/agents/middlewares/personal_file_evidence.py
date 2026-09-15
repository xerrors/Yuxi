"""个人文件删除后的最终模型发送屏障。"""

from langchain.agents.middleware.types import AgentMiddleware

from yuxi.services.personal_file_evidence_service import (
    PersonalFileEvidenceUnavailable,
    validate_personal_file_evidence,
)


class PersonalFileEvidenceMiddleware(AgentMiddleware):
    """重试和输入变换后重新核对个人历史；同步路径不静默跳过。"""

    async def awrap_model_call(self, request, handler):
        await validate_personal_file_evidence(getattr(request.runtime, "context", None))
        return await handler(request)

    def wrap_model_call(self, request, handler):
        if getattr(getattr(request, "runtime", None), "context", None) is not None:
            raise PersonalFileEvidenceUnavailable("个人文件来源复核需要异步模型调用")
        return handler(request)
