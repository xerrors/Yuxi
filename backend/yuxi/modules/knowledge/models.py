"""知识 PostgreSQL 映射。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)

from yuxi.infrastructure.postgres.base import JSON_VALUE
from yuxi.infrastructure.postgres.base import KnowledgeBase as Base
from yuxi.shared.datetime import utc_now


class KnowledgeBase(Base):
    """知识库模型"""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    kb_type = Column(String(32), nullable=False, index=True)
    embedding_model_spec = Column(String(512))
    llm_model_spec = Column(String(512))
    query_params = Column(JSON_VALUE)
    additional_params = Column(JSON_VALUE)
    share_config = Column(JSON_VALUE)
    sample_questions = Column(JSON_VALUE)
    metadata_fields = Column(JSON_VALUE, nullable=False, default=list, server_default=text("'[]'"))
    deleted_at = Column(DateTime(timezone=True), index=True)
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        # 部分唯一索引：未删除知识库的小写名称全局唯一；软删除后名称可被新库复用。
        Index(
            "uq_knowledge_bases_active_name",
            func.lower(name),
            unique=True,
            postgresql_where=deleted_at.is_(None),
            sqlite_where=deleted_at.is_(None),
        ),
    )


class KnowledgeFile(Base):
    """知识文件模型，同时充当目录树节点：is_folder 区分目录，parent_id 构成层级。"""

    __tablename__ = "knowledge_files"
    __table_args__ = (
        # 同一父目录（含 parent_id 为空的根层）内，未删除文件名经 NFC 与小写归一后唯一；
        # NULLS NOT DISTINCT 让根层同名同样冲突。表达式依赖 PostgreSQL，SQLite 测试库跳过。
        Index(
            "uq_knowledge_files_active_name",
            "kb_id",
            "parent_id",
            text("normalize(lower(normalize(btrim(filename), NFC)), NFC)"),
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("deleted_at IS NULL"),
        ).ddl_if(dialect="postgresql"),
        # 未删除行的文件名必须是干净叶子名：无路径分隔符与控制字符、NFC 归一、无首尾空白。
        CheckConstraint(
            "deleted_at IS NOT NULL OR (filename = btrim(filename) AND filename NOT IN ('', '.', '..') "
            "AND strpos(filename, '/') = 0 AND strpos(filename, chr(92)) = 0 "
            "AND filename !~ '[[:cntrl:]]' AND filename = normalize(filename, NFC))",
            name="ck_knowledge_files_leaf_name",
        ).ddl_if(dialect="postgresql"),
        # file_id 单列唯一支撑子表的单列外键引用；(file_id, kb_id) 复合唯一再钉住两者的绑定。
        UniqueConstraint("file_id", name="uq_knowledge_files_file_id"),
        UniqueConstraint("file_id", "kb_id", name="uq_knowledge_files_file_id_kb"),
        # 复合外键保证子节点与父节点同属一个知识库；DEFERRED 允许同一事务内以任意顺序写入目录树。
        ForeignKeyConstraint(
            ["parent_id", "kb_id"],
            ["knowledge_files.file_id", "knowledge_files.kb_id"],
            name="fk_knowledge_files_parent_kb",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    filename = Column(String(512), nullable=False)
    original_filename = Column(String(512))
    file_type = Column(String(64))
    path = Column(String(1024))
    minio_url = Column(String(1024))
    markdown_file = Column(String(1024))
    status = Column(String(32), default="uploaded", index=True)
    content_hash = Column(String(128), index=True)
    file_size = Column(BigInteger)
    chunk_count = Column(Integer, default=0)
    token_count = Column(BigInteger, default=0)
    content_type = Column(String(64))
    processing_params = Column(JSON_VALUE)
    attributes = Column(JSON_VALUE, nullable=False, default=dict, server_default=text("'{}'"))
    is_folder = Column(Boolean, default=False)
    error_message = Column(Text)
    processing_job_id = Column(String(64), index=True)
    processing_owner = Column(String(128))
    created_by = Column(String(128))
    updated_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    # generation 是文件事实的单调代次；building_generation 记录在途重建，投影成功后晋升 active_generation。
    generation = Column(Integer, nullable=False, default=1, server_default=text("1"))
    active_generation = Column(Integer, nullable=False, default=1, server_default=text("1"))
    building_generation = Column(Integer)
    # 软删除先在 PostgreSQL 生效，向量库等外部投影随后异步清理。
    deleted_at = Column(DateTime(timezone=True), index=True)
    projection_status = Column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    projection_error = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class KnowledgeImport(Base):
    """持久导入回执；删除文件或知识库不能复活同一请求。"""

    __tablename__ = "knowledge_imports"
    __table_args__ = (UniqueConstraint("operator_id", "import_id", name="uq_knowledge_import_identity"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    operator_id = Column(String(128), nullable=False)
    import_id = Column(String(128), nullable=False)
    kb_id = Column(String(80), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    response = Column(JSON_VALUE, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeProjectionOutbox(Base):
    """PostgreSQL 权威事实到外部投影的可重试意图。"""

    __tablename__ = "knowledge_projection_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'applied')", name="ck_knowledge_projection_outbox_status"),
        UniqueConstraint("event_key", name="uq_knowledge_projection_outbox_event_key"),
        Index("ix_knowledge_projection_outbox_pending", "status", "updated_at", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(192), nullable=False)
    kb_id = Column(String(80), nullable=False, index=True)
    aggregate_id = Column(String(128), nullable=False)
    generation = Column(Integer, nullable=False)
    operation = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    last_error = Column(Text)
    applied_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class KnowledgeChunk(Base):
    """文件内容的分块投影，按文件代次生成。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_knowledge_chunks_chunk_id"),
        UniqueConstraint("chunk_id", "file_id", "kb_id", name="uq_knowledge_chunks_chunk_file_kb"),
        ForeignKeyConstraint(
            ["file_id", "kb_id"],
            ["knowledge_files.file_id", "knowledge_files.kb_id"],
            name="fk_knowledge_chunks_file_kb",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_chunks_file_id", "file_id"),
        Index("ix_knowledge_chunks_kb_id", "kb_id"),
        Index("ix_knowledge_chunks_graph_indexed", "graph_indexed"),
        Index("ix_knowledge_chunks_graph_structure_indexed", "graph_structure_indexed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    # 与所属文件的 active_generation 对齐，旧代次 chunk 在投影切换后清理。
    generation = Column(Integer, nullable=False, default=1, server_default=text("1"), index=True)
    content = Column(Text, nullable=False)
    start_line = Column(Integer)
    end_line = Column(Integer)
    start_char_pos = Column(Integer)
    end_char_pos = Column(Integer)
    start_token_pos = Column(Integer)
    end_token_pos = Column(Integer)
    # 图谱构建分两阶段：结构（实体/三元组/提及）写入后置 structure 标记，向量回填完成才置 graph_indexed。
    graph_structure_indexed = Column(Boolean, default=False, nullable=False)
    graph_indexed = Column(Boolean, default=False)
    graph_extraction_details = Column(
        JSON_VALUE,
        default=lambda: {"status": "pending", "attempt_count": 0},
        nullable=False,
    )
    ent_ids = Column(JSON_VALUE)
    tags = Column(JSON_VALUE)
    extraction_result = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class KnowledgeGraphEntity(Base):
    """知识图谱实体"""

    __tablename__ = "knowledge_graph_entities"
    __table_args__ = (
        UniqueConstraint("entity_id", name="uq_knowledge_graph_entities_entity_id"),
        UniqueConstraint("entity_id", "kb_id", name="uq_knowledge_graph_entities_entity_id_kb"),
        UniqueConstraint("kb_id", "normalized_name", "label", name="uq_knowledge_graph_entities_identity"),
        Index("ix_knowledge_graph_entities_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entities_vector_pending", "kb_id", "vector_status", "vector_next_retry_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    label = Column(String(128), nullable=False)
    name = Column(String(512), nullable=False)
    attributes = Column(JSON_VALUE)
    # 向量回填的租约状态：失败按 vector_next_retry_at 退避重试，locked_until 与 lock_token 标识当前执行者。
    vector_status = Column(String(16), nullable=False, default="pending")
    vector_attempt_count = Column(Integer, nullable=False, default=0)
    vector_last_error = Column(Text)
    vector_next_retry_at = Column(DateTime(timezone=True))
    vector_locked_until = Column(DateTime(timezone=True))
    vector_lock_token = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class KnowledgeGraphEntityMention(Base):
    """知识图谱实体在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id", name="uq_knowledge_graph_entity_mentions_entity_chunk"),
        ForeignKeyConstraint(
            ["entity_id", "kb_id"],
            ["knowledge_graph_entities.entity_id", "knowledge_graph_entities.kb_id"],
            name="fk_knowledge_graph_entity_mentions_entity_kb",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["file_id", "kb_id"],
            ["knowledge_files.file_id", "knowledge_files.kb_id"],
            name="fk_knowledge_graph_entity_mentions_file_kb",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "file_id", "kb_id"],
            ["knowledge_chunks.chunk_id", "knowledge_chunks.file_id", "knowledge_chunks.kb_id"],
            name="fk_knowledge_graph_entity_mentions_chunk_file_kb",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_graph_entity_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entity_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_entity_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class KnowledgeGraphTriple(Base):
    """知识图谱三元组"""

    __tablename__ = "knowledge_graph_triples"
    __table_args__ = (
        UniqueConstraint("triple_id", name="uq_knowledge_graph_triples_triple_id"),
        UniqueConstraint("triple_id", "kb_id", name="uq_knowledge_graph_triples_triple_id_kb"),
        ForeignKeyConstraint(
            ["source_entity_id", "kb_id"],
            ["knowledge_graph_entities.entity_id", "knowledge_graph_entities.kb_id"],
            name="fk_knowledge_graph_triples_source_entity_kb",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_entity_id", "kb_id"],
            ["knowledge_graph_entities.entity_id", "knowledge_graph_entities.kb_id"],
            name="fk_knowledge_graph_triples_target_entity_kb",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_graph_triples_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triples_vector_pending", "kb_id", "vector_status", "vector_next_retry_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    source_entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    target_entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    vector_status = Column(String(16), nullable=False, default="pending")
    vector_attempt_count = Column(Integer, nullable=False, default=0)
    vector_last_error = Column(Text)
    vector_next_retry_at = Column(DateTime(timezone=True))
    vector_locked_until = Column(DateTime(timezone=True))
    vector_lock_token = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class KnowledgeGraphTripleMention(Base):
    """知识图谱三元组在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_triple_mentions"
    __table_args__ = (
        UniqueConstraint("triple_id", "chunk_id", name="uq_knowledge_graph_triple_mentions_triple_chunk"),
        ForeignKeyConstraint(
            ["triple_id", "kb_id"],
            ["knowledge_graph_triples.triple_id", "knowledge_graph_triples.kb_id"],
            name="fk_knowledge_graph_triple_mentions_triple_kb",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["file_id", "kb_id"],
            ["knowledge_files.file_id", "knowledge_files.kb_id"],
            name="fk_knowledge_graph_triple_mentions_file_kb",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "file_id", "kb_id"],
            ["knowledge_chunks.chunk_id", "knowledge_chunks.file_id", "knowledge_chunks.kb_id"],
            name="fk_knowledge_graph_triple_mentions_chunk_file_kb",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_graph_triple_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triple_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_triple_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), ForeignKey("knowledge_graph_triples.triple_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    text = Column(Text)
    extractor_type = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now)


class EvaluationDataset(Base):
    """评估数据集模型"""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("dataset_id", "kb_id", name="uq_evaluation_datasets_dataset_id_kb"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    item_count = Column(Integer, default=0)
    has_gold_chunks = Column(Boolean, default=False)
    has_gold_answers = Column(Boolean, default=False)
    build_metadata = Column(JSON_VALUE)
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class EvaluationDatasetItem(Base):
    """评估数据集题目模型"""

    __tablename__ = "evaluation_dataset_items"
    __table_args__ = (
        UniqueConstraint("item_id", "kb_id", name="uq_evaluation_dataset_items_item_id_kb"),
        UniqueConstraint("item_id", "dataset_id", name="uq_evaluation_dataset_items_item_dataset"),
        ForeignKeyConstraint(
            ["dataset_id", "kb_id"],
            ["evaluation_datasets.dataset_id", "evaluation_datasets.kb_id"],
            name="fk_evaluation_dataset_items_dataset_kb",
            ondelete="CASCADE",
        ),
        UniqueConstraint("dataset_id", "item_index", name="uq_evaluation_dataset_items_dataset_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now)


class EvaluationRun(Base):
    """评估运行模型"""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "kb_id", name="uq_evaluation_runs_run_id_kb"),
        UniqueConstraint("run_id", "dataset_id", name="uq_evaluation_runs_run_dataset"),
        ForeignKeyConstraint(
            ["dataset_id", "kb_id"],
            ["evaluation_datasets.dataset_id", "evaluation_datasets.kb_id"],
            name="fk_evaluation_runs_dataset_kb",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="SET NULL"),
        index=True,
    )
    status = Column(String(32), default="running", index=True)
    retrieval_config = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    overall_score = Column(Float)
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    completed_at = Column(DateTime(timezone=True))
    created_by = Column(String(128))


class EvaluationRunItem(Base):
    """评估逐题结果模型"""

    __tablename__ = "evaluation_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "item_index", name="uq_evaluation_run_items_run_index"),
        ForeignKeyConstraint(
            ["run_id", "dataset_id"],
            ["evaluation_runs.run_id", "evaluation_runs.dataset_id"],
            name="fk_evaluation_run_items_run_dataset",
            onupdate="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["dataset_item_id", "dataset_id"],
            ["evaluation_dataset_items.item_id", "evaluation_dataset_items.dataset_id"],
            name="fk_evaluation_run_items_item_dataset",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # dataset_item_id 与 dataset_id 只能同时有值或同时为空：MATCH FULL 复合外键在原题删除时整组置空，逐题快照自包含。
    dataset_item_id = Column(String(64), index=True)
    dataset_id = Column(String(64), index=True)
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    generated_answer = Column(Text)
    retrieved_chunks = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now)
