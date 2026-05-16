from __future__ import annotations

from types import SimpleNamespace

from yuxi.knowledge.implementations.lightrag import LightRagKB


def test_lightrag_attaches_file_id_to_chunks_from_chunk_store() -> None:
    rag = SimpleNamespace(
        text_chunks=SimpleNamespace(
            _data={
                "chunk-1": {"full_doc_id": "file-1"},
                "chunk-2": {"full_doc_id": "file-2"},
            }
        )
    )
    chunks = [
        {"chunk_id": "chunk-1", "content": "one"},
        {"id": "chunk-2", "content": "two", "metadata": {}},
    ]

    result = LightRagKB._attach_file_ids_to_chunks(rag, chunks)

    assert result[0]["metadata"] == {"chunk_id": "chunk-1", "file_id": "file-1"}
    assert result[1]["metadata"] == {"chunk_id": "chunk-2", "file_id": "file-2"}


def test_lightrag_keeps_existing_file_id_when_mapping_chunks() -> None:
    rag = SimpleNamespace(text_chunks=SimpleNamespace(_data={"chunk-1": {"full_doc_id": "file-1"}}))
    chunks = [{"metadata": {"chunk_id": "chunk-1", "file_id": "existing-file"}}]

    result = LightRagKB._attach_file_ids_to_chunks(rag, chunks)

    assert result[0]["metadata"] == {"chunk_id": "chunk-1", "file_id": "existing-file"}
