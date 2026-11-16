from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yuxi.workers.background_job_context import BackgroundJobContext

JobHandler = Callable[["BackgroundJobContext"], Awaitable[object]]
JobSuccessHandler = Callable[[object, object, object], Awaitable[None]]
JobFailureHandler = Callable[[object, object, str], Awaitable[None]]


@dataclass(frozen=True)
class JobDefinition:
    """描述可持久重建的任务 Handler。"""

    job_type: str
    module: str
    function: str
    success_function: str | None = None
    failure_function: str | None = None
    version: int = 1

    def load_handler(self) -> JobHandler:
        return self._load(self.function, "handler")

    def load_success_handler(self) -> JobSuccessHandler | None:
        return self._load(self.success_function, "success handler") if self.success_function is not None else None

    def load_failure_handler(self) -> JobFailureHandler | None:
        return self._load(self.failure_function, "failure handler") if self.failure_function is not None else None

    def _load(self, function: str, label: str):
        """惰性加载并校验一个注册 Handler。"""
        handler = getattr(import_module(self.module), function)
        if not callable(handler):
            raise TypeError(f"Job {label} is not callable: {self.module}:{function}")
        return handler


def get_job_definition(job_type: str, handler_version: int | None = None) -> JobDefinition:
    """返回当前 shipping JobDefinition，并拒绝未知类型或版本。"""
    definition = JOB_DEFINITIONS.get(job_type)
    if definition is None:
        raise ValueError(f"Unknown job type: {job_type}")
    if handler_version is not None and definition.version != handler_version:
        raise ValueError(f"Unsupported handler version for {job_type}: {handler_version}; expected {definition.version}")
    return definition


JOB_DEFINITIONS = {
    definition.job_type: definition
    for definition in (
        JobDefinition(
            "knowledge_media_index",
            "yuxi.modules.knowledge.media.jobs",
            "run_media_index",
            success_function="finish_media_index",
            failure_function="fail_media_index",
        ),
        JobDefinition(
            "knowledge_ingest",
            "yuxi.modules.knowledge.services.background_jobs",
            "run_knowledge_ingest",
            version=2,
            failure_function="fail_knowledge_file_job",
        ),
        JobDefinition(
            "knowledge_parse",
            "yuxi.modules.knowledge.services.background_jobs",
            "run_knowledge_parse",
            failure_function="fail_knowledge_file_job",
        ),
        JobDefinition(
            "knowledge_index",
            "yuxi.modules.knowledge.services.background_jobs",
            "run_knowledge_index",
            failure_function="fail_knowledge_file_job",
        ),
        JobDefinition(
            "knowledge_document_reset",
            "yuxi.modules.knowledge.services.document_resets",
            "run_document_reset",
            success_function="finish_document_reset",
            failure_function="fail_document_reset",
        ),
        JobDefinition(
            "knowledge_graph_index",
            "yuxi.modules.knowledge.services.background_jobs",
            "run_knowledge_graph",
        ),
        JobDefinition(
            "dataset_generation",
            "yuxi.modules.knowledge.evaluation.service",
            "run_dataset_generation_job",
            success_function="finish_dataset_generation_job",
            failure_function="fail_dataset_generation_job",
        ),
        JobDefinition(
            "rag_evaluation",
            "yuxi.modules.knowledge.evaluation.service",
            "run_rag_evaluation_job",
            success_function="finish_rag_evaluation_job",
            failure_function="fail_rag_evaluation_job",
        ),
    )
}
