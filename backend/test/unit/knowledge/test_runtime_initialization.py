from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.knowledge.parser.registry import PROCESSOR_TYPES

pytestmark = pytest.mark.unit


def test_document_processor_factory_uses_shared_registry():
    assert DocumentProcessorFactory.PROCESSOR_TYPES is PROCESSOR_TYPES


def test_knowledge_runtime_preserves_lite_mode(tmp_path):
    env = os.environ.copy()
    env["LITE_MODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from yuxi.knowledge.runtime import knowledge_base; "
                "from yuxi.knowledge.factory import KnowledgeBaseFactory; "
                "print(json.dumps({"
                "'manager': type(knowledge_base).__name__, "
                "'types': sorted(KnowledgeBaseFactory.get_available_types())"
                "}))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    loaded = json.loads(result.stdout.splitlines()[-1])
    assert loaded == {"manager": "KnowledgeBaseManager", "types": ["dify", "notion"]}
