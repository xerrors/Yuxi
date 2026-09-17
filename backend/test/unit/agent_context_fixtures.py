"""流服务测试使用已由 worker 准备的运行输入。"""

from yuxi.agents.context import BaseContext
from yuxi.services.agent_run_manifest_service import PreparedRunExecution


def prepared_execution(*, backend_id="ChatbotAgent", **config):
    """构造具有稳定身份和 Workdir 的执行上下文。"""
    context = BaseContext(
        thread_id="thread-1",
        uid="user-1",
        run_id="run-1",
        request_id="req-1",
        worker_id="worker-1",
        runtime_scope_id="thread-1",
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
        workdir_path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
    )
    context.update(config)
    return PreparedRunExecution(manifest={}, context=context, backend_id=backend_id)
