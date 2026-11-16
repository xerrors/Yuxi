CREATE TABLE public.agent_envs (
    id integer NOT NULL,
    uid character varying NOT NULL,
    env json NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.agent_envs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_envs_id_seq OWNED BY public.agent_envs.id;

CREATE TABLE public.agent_run_attempts (
    id integer NOT NULL,
    run_id character varying(64) NOT NULL,
    attempt_no integer NOT NULL,
    worker_id character varying(128) NOT NULL,
    started_at timestamp without time zone NOT NULL,
    heartbeat_at timestamp without time zone,
    lease_expires_at timestamp without time zone,
    finished_at timestamp without time zone,
    outcome character varying(32),
    error_type character varying(64),
    error_message text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.agent_run_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_run_attempts_id_seq OWNED BY public.agent_run_attempts.id;

CREATE TABLE public.agent_run_requests (
    id integer NOT NULL,
    request_id character varying(64) NOT NULL,
    uid character varying(64) NOT NULL,
    agent_slug character varying(64) NOT NULL,
    conversation_thread_id character varying(64) NOT NULL,
    source character varying(32) NOT NULL,
    channel character varying(32) NOT NULL,
    external_id character varying(128),
    origin_metadata json NOT NULL,
    queue_policy character varying(16) NOT NULL,
    status character varying(32) NOT NULL,
    input_message_id integer NOT NULL,
    dispatched_run_id character varying(64),
    input_payload json NOT NULL,
    error_message text,
    created_at timestamp without time zone NOT NULL,
    dispatched_at timestamp without time zone,
    updated_at timestamp without time zone NOT NULL
);

CREATE SEQUENCE public.agent_run_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_run_requests_id_seq OWNED BY public.agent_run_requests.id;

CREATE TABLE public.agent_runs (
    id character varying(64) NOT NULL,
    conversation_thread_id character varying(64) NOT NULL,
    runtime_scope_id character varying(64) NOT NULL,
    runtime_cleanup_pending boolean DEFAULT false NOT NULL,
    agent_slug character varying(64) NOT NULL,
    uid character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    request_id character varying(64) NOT NULL,
    source character varying(32) NOT NULL,
    channel character varying(32) NOT NULL,
    external_id character varying(128),
    origin_metadata json NOT NULL,
    conversation_id integer,
    created_by_run_id character varying(64),
    subagent_thread_relation_id integer,
    run_type character varying(32) NOT NULL,
    input_message_id integer,
    output_message_id integer,
    input_payload json NOT NULL,
    token_usage jsonb NOT NULL,
    error_type character varying(64),
    error_message text,
    worker_id character varying(128),
    heartbeat_at timestamp without time zone,
    lease_expires_at timestamp without time zone,
    manifest jsonb,
    manifest_fingerprint character varying(64),
    manifest_recorded_at timestamp without time zone,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    langfuse_trace_id character varying(64),
    prepared_at timestamp without time zone,
    first_output_at timestamp without time zone,
    first_model_request_at timestamp without time zone,
    CONSTRAINT ck_agent_runs_nonterminal_shape CHECK ((((status)::text = ANY ((ARRAY['completed'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'interrupted'::character varying])::text[])) OR (((runtime_scope_id)::text <> ''::text) AND ((conversation_thread_id)::text <> ''::text) AND ((((run_type)::text = 'chat'::text) AND ((runtime_scope_id)::text = (conversation_thread_id)::text) AND (created_by_run_id IS NULL) AND (subagent_thread_relation_id IS NULL)) OR (((run_type)::text = 'resume'::text) AND ((runtime_scope_id)::text = (conversation_thread_id)::text) AND (created_by_run_id IS NOT NULL) AND (subagent_thread_relation_id IS NULL)) OR (((run_type)::text = 'subagent'::text) AND (created_by_run_id IS NOT NULL) AND (subagent_thread_relation_id IS NOT NULL))))))
);

CREATE TABLE public.agents (
    id integer NOT NULL,
    slug character varying(80) NOT NULL,
    backend_id character varying(64) NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    icon character varying(255),
    pics json NOT NULL,
    config_json json NOT NULL,
    share_config jsonb NOT NULL,
    is_default boolean NOT NULL,
    is_subagent boolean NOT NULL,
    created_by character varying(64),
    updated_by character varying(64),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.agents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agents_id_seq OWNED BY public.agents.id;

CREATE TABLE public.api_keys (
    id integer NOT NULL,
    key_hash character varying(64) NOT NULL,
    key_prefix character varying(16) NOT NULL,
    request_id character varying(64),
    intent_hash character varying(64),
    name character varying(100) NOT NULL,
    user_id integer NOT NULL,
    department_id integer,
    expires_at timestamp without time zone,
    is_enabled boolean NOT NULL,
    revoked_at timestamp without time zone,
    last_used_at timestamp without time zone,
    created_by character varying(64) NOT NULL,
    created_at timestamp without time zone
);

CREATE SEQUENCE public.api_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.api_keys_id_seq OWNED BY public.api_keys.id;

CREATE TABLE public.checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea
);

CREATE TABLE public.checkpoint_migrations (
    v integer NOT NULL
);

CREATE TABLE public.checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text DEFAULT ''::text NOT NULL
);

CREATE TABLE public.checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);

CREATE TABLE public.cli_auth_sessions (
    id integer NOT NULL,
    device_code_hash character varying(64) NOT NULL,
    user_code character varying(16) NOT NULL,
    status character varying(32) NOT NULL,
    key_name character varying(100) NOT NULL,
    approved_user_id integer,
    api_key_id integer,
    created_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    approved_at timestamp without time zone,
    consumed_at timestamp without time zone
);

CREATE SEQUENCE public.cli_auth_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.cli_auth_sessions_id_seq OWNED BY public.cli_auth_sessions.id;

CREATE TABLE public.config_options (
    id integer NOT NULL,
    key character varying(100) NOT NULL,
    name character varying(100) NOT NULL,
    description text NOT NULL,
    params json NOT NULL,
    value json NOT NULL,
    created_by character varying(100),
    updated_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.config_options_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.config_options_id_seq OWNED BY public.config_options.id;

CREATE TABLE public.conversation_stats (
    id integer NOT NULL,
    conversation_id integer NOT NULL,
    message_count integer,
    total_tokens integer,
    model_used character varying(100),
    user_feedback json,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.conversation_stats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.conversation_stats_id_seq OWNED BY public.conversation_stats.id;

CREATE TABLE public.conversations (
    id integer NOT NULL,
    thread_id character varying(64) NOT NULL,
    creation_request_id character varying(64),
    uid character varying(64) NOT NULL,
    agent_id character varying(64) NOT NULL,
    title character varying(255),
    status character varying(20),
    is_pinned boolean NOT NULL,
    last_viewed_run_id character varying(64),
    project_id character varying(64) NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    extra_metadata json
);

CREATE SEQUENCE public.conversations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.conversations_id_seq OWNED BY public.conversations.id;

CREATE TABLE public.departments (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    description character varying(255),
    created_at timestamp without time zone
);

CREATE SEQUENCE public.departments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.departments_id_seq OWNED BY public.departments.id;

CREATE TABLE public.evaluation_dataset_items (
    id integer NOT NULL,
    item_id character varying(64) NOT NULL,
    dataset_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    item_index integer NOT NULL,
    query_text text NOT NULL,
    gold_chunk_ids jsonb,
    gold_answer text,
    created_at timestamp with time zone
);

CREATE SEQUENCE public.evaluation_dataset_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.evaluation_dataset_items_id_seq OWNED BY public.evaluation_dataset_items.id;

CREATE TABLE public.evaluation_datasets (
    id integer NOT NULL,
    dataset_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    item_count integer,
    has_gold_chunks boolean,
    has_gold_answers boolean,
    build_metadata jsonb,
    created_by character varying(64),
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);

CREATE SEQUENCE public.evaluation_datasets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.evaluation_datasets_id_seq OWNED BY public.evaluation_datasets.id;

CREATE TABLE public.evaluation_run_items (
    id integer NOT NULL,
    run_id character varying(64) NOT NULL,
    dataset_item_id character varying(64),
    item_index integer NOT NULL,
    query_text text NOT NULL,
    gold_chunk_ids jsonb,
    gold_answer text,
    generated_answer text,
    retrieved_chunks jsonb,
    metrics jsonb,
    created_at timestamp with time zone
);

CREATE SEQUENCE public.evaluation_run_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.evaluation_run_items_id_seq OWNED BY public.evaluation_run_items.id;

CREATE TABLE public.evaluation_runs (
    id integer NOT NULL,
    run_id character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    kb_id character varying(80) NOT NULL,
    dataset_id character varying(64),
    status character varying(32),
    retrieval_config jsonb,
    metrics jsonb,
    overall_score double precision,
    total_items integer,
    completed_items integer,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_by character varying(64)
);

CREATE SEQUENCE public.evaluation_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.evaluation_runs_id_seq OWNED BY public.evaluation_runs.id;

CREATE TABLE public.knowledge_bases (
    id integer NOT NULL,
    kb_id character varying(80) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    kb_type character varying(32) NOT NULL,
    embedding_model_spec character varying(512),
    llm_model_spec character varying(512),
    query_params jsonb,
    additional_params jsonb,
    share_config jsonb,
    mindmap jsonb,
    mindmap_file_ids jsonb,
    mindmap_metadata jsonb,
    sample_questions jsonb,
    created_by character varying(64),
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_bases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_bases_id_seq OWNED BY public.knowledge_bases.id;

CREATE TABLE public.knowledge_child_chunks (
    child_id character varying(64) NOT NULL,
    parent_id character varying(64) NOT NULL,
    version_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    doc_id character varying(64) NOT NULL,
    child_index integer NOT NULL,
    child_text text NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    token_count integer NOT NULL,
    spans jsonb NOT NULL,
    metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.knowledge_chunks (
    id integer NOT NULL,
    chunk_id character varying(128) NOT NULL,
    file_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    start_char_pos integer,
    end_char_pos integer,
    start_token_pos integer,
    end_token_pos integer,
    graph_structure_indexed boolean DEFAULT false NOT NULL,
    graph_indexed boolean,
    graph_extraction_details jsonb DEFAULT jsonb_build_object('status', 'pending', 'attempt_count', 0) NOT NULL,
    ent_ids jsonb,
    tags jsonb,
    extraction_result jsonb,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_chunks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_chunks_id_seq OWNED BY public.knowledge_chunks.id;

CREATE TABLE public.knowledge_document_versions (
    version_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    doc_id character varying(64) NOT NULL,
    indexing_path character varying(32) NOT NULL,
    embedding_model_spec character varying(512) NOT NULL,
    embedding_dimension integer NOT NULL,
    chunk_preset_id character varying(32) NOT NULL,
    processing_params jsonb NOT NULL,
    status character varying(32) NOT NULL,
    activated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.knowledge_files (
    id integer NOT NULL,
    file_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    parent_id character varying(64),
    filename character varying(512) NOT NULL,
    original_filename character varying(512),
    file_type character varying(64),
    path character varying(1024),
    minio_url character varying(1024),
    markdown_file character varying(1024),
    status character varying(32),
    content_hash character varying(128),
    file_size bigint,
    chunk_count integer,
    token_count bigint,
    content_type character varying(64),
    processing_params jsonb,
    is_folder boolean,
    error_message text,
    created_by character varying(64),
    updated_by character varying(64),
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    processing_task_id character varying(64),
    processing_owner character varying(128)
);

CREATE SEQUENCE public.knowledge_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_files_id_seq OWNED BY public.knowledge_files.id;

CREATE TABLE public.knowledge_graph_entities (
    id integer NOT NULL,
    entity_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    normalized_name character varying(512) NOT NULL,
    label character varying(128) NOT NULL,
    name character varying(512) NOT NULL,
    attributes jsonb,
    vector_status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    vector_attempt_count integer DEFAULT 0 NOT NULL,
    vector_last_error text,
    vector_next_retry_at timestamp with time zone,
    vector_locked_until timestamp with time zone,
    vector_lock_token character varying(32),
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_graph_entities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_graph_entities_id_seq OWNED BY public.knowledge_graph_entities.id;

CREATE TABLE public.knowledge_graph_entity_mentions (
    id integer NOT NULL,
    entity_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    chunk_id character varying(128) NOT NULL,
    created_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_graph_entity_mentions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_graph_entity_mentions_id_seq OWNED BY public.knowledge_graph_entity_mentions.id;

CREATE TABLE public.knowledge_graph_triple_mentions (
    id integer NOT NULL,
    triple_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    chunk_id character varying(128) NOT NULL,
    text text,
    extractor_type character varying(128),
    created_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_graph_triple_mentions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_graph_triple_mentions_id_seq OWNED BY public.knowledge_graph_triple_mentions.id;

CREATE TABLE public.knowledge_graph_triples (
    id integer NOT NULL,
    triple_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    source_entity_id character varying(64) NOT NULL,
    target_entity_id character varying(64) NOT NULL,
    relation_type character varying(256) NOT NULL,
    content text NOT NULL,
    vector_status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    vector_attempt_count integer DEFAULT 0 NOT NULL,
    vector_last_error text,
    vector_next_retry_at timestamp with time zone,
    vector_locked_until timestamp with time zone,
    vector_lock_token character varying(32),
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);

CREATE SEQUENCE public.knowledge_graph_triples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_graph_triples_id_seq OWNED BY public.knowledge_graph_triples.id;

CREATE TABLE public.knowledge_parent_chunks (
    parent_id character varying(64) NOT NULL,
    version_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    doc_id character varying(64) NOT NULL,
    parent_index integer NOT NULL,
    parent_text text NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    token_count integer NOT NULL,
    metadata jsonb NOT NULL,
    graph_structure_indexed boolean DEFAULT false NOT NULL,
    graph_indexed boolean DEFAULT false NOT NULL,
    graph_extraction_details jsonb DEFAULT jsonb_build_object('status', 'pending', 'attempt_count', 0) NOT NULL,
    ent_ids jsonb,
    tags jsonb,
    extraction_result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.knowledge_parent_graph_entity_mentions (
    id integer NOT NULL,
    entity_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    parent_id character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);

CREATE SEQUENCE public.knowledge_parent_graph_entity_mentions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_parent_graph_entity_mentions_id_seq OWNED BY public.knowledge_parent_graph_entity_mentions.id;

CREATE TABLE public.knowledge_parent_graph_triple_mentions (
    id integer NOT NULL,
    triple_id character varying(64) NOT NULL,
    kb_id character varying(80) NOT NULL,
    file_id character varying(64) NOT NULL,
    parent_id character varying(64) NOT NULL,
    text text,
    extractor_type character varying(128),
    created_at timestamp with time zone DEFAULT now()
);

CREATE SEQUENCE public.knowledge_parent_graph_triple_mentions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.knowledge_parent_graph_triple_mentions_id_seq OWNED BY public.knowledge_parent_graph_triple_mentions.id;

CREATE TABLE public.mcp_servers (
    id integer NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(100) NOT NULL,
    description character varying(500),
    transport character varying(20) NOT NULL,
    url character varying(500),
    command character varying(500),
    args json,
    env json,
    headers json,
    timeout integer,
    sse_read_timeout integer,
    tags json,
    icon character varying(50),
    enabled integer NOT NULL,
    disabled_tools json,
    created_by character varying(100) NOT NULL,
    updated_by character varying(100) NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.mcp_servers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.mcp_servers_id_seq OWNED BY public.mcp_servers.id;

CREATE TABLE public.message_feedbacks (
    id integer NOT NULL,
    message_id integer NOT NULL,
    uid character varying(64) NOT NULL,
    rating character varying(10) NOT NULL,
    reason text,
    created_at timestamp without time zone
);

CREATE SEQUENCE public.message_feedbacks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.message_feedbacks_id_seq OWNED BY public.message_feedbacks.id;

CREATE TABLE public.messages (
    id integer NOT NULL,
    conversation_id integer NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    message_type character varying(30),
    created_at timestamp without time zone,
    token_count integer,
    extra_metadata json,
    image_content text,
    run_id character varying(64),
    request_id character varying(64),
    delivery_status character varying(32) NOT NULL,
    operation_id character varying(128),
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    duration_ms bigint,
    sequence bigint,
    execution_status character varying(32),
    usage jsonb,
    CONSTRAINT ck_messages_execution_status CHECK (((execution_status IS NULL) OR ((execution_status)::text = ANY ((ARRAY['running'::character varying, 'completed'::character varying, 'failed'::character varying, 'interrupted'::character varying, 'abandoned'::character varying])::text[]))))
);

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;

CREATE TABLE public.model_providers (
    id integer NOT NULL,
    provider_id character varying(100) NOT NULL,
    display_name character varying(100) NOT NULL,
    provider_type character varying(32) NOT NULL,
    default_protocol character varying(64),
    base_url character varying(500) NOT NULL,
    embedding_base_url character varying(500),
    rerank_base_url character varying(500),
    models_endpoint character varying(200),
    embedding_models_endpoint character varying(200),
    rerank_models_endpoint character varying(200),
    api_key_env character varying(128),
    api_key character varying(500),
    capabilities json NOT NULL,
    enabled_models json NOT NULL,
    headers_json json,
    extra_json json,
    is_enabled boolean NOT NULL,
    is_builtin boolean NOT NULL,
    created_by character varying(100),
    updated_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.model_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_providers_id_seq OWNED BY public.model_providers.id;

CREATE TABLE public.operation_logs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    operation character varying NOT NULL,
    details text,
    ip_address character varying,
    "timestamp" timestamp without time zone
);

CREATE SEQUENCE public.operation_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.operation_logs_id_seq OWNED BY public.operation_logs.id;

CREATE TABLE public.projects (
    id character varying(64) NOT NULL,
    uid character varying(64) NOT NULL,
    name character varying(255),
    selection_status character varying(20) NOT NULL,
    workdir_path character varying(512) NOT NULL,
    directory_mode character varying(20) NOT NULL,
    idempotency_key character varying(128),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    deleted_at timestamp without time zone,
    CONSTRAINT ck_projects_directory_mode CHECK (((directory_mode)::text = ANY ((ARRAY['managed'::character varying, 'linked'::character varying])::text[]))),
    CONSTRAINT ck_projects_selection_status CHECK (((selection_status)::text = ANY ((ARRAY['implicit'::character varying, 'selectable'::character varying])::text[]))),
    CONSTRAINT ck_projects_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'deleted'::character varying])::text[])))
);

CREATE TABLE public.scheduled_agent_jobs (
    id character varying(64) NOT NULL,
    uid character varying(64) NOT NULL,
    creation_request_id character varying(64) NOT NULL,
    creation_intent_hash character varying(64) NOT NULL,
    project_id character varying(64) NOT NULL,
    agent_slug character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    prompt text NOT NULL,
    tool_approval_mode character varying(32) DEFAULT 'default'::character varying NOT NULL,
    model_spec character varying(512),
    cron_expression character varying(100) NOT NULL,
    timezone character varying(64) NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    deleted_at timestamp without time zone,
    next_run_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_scheduled_agent_jobs_tool_approval_mode CHECK (((tool_approval_mode)::text = ANY ((ARRAY['default'::character varying, 'always_trust'::character varying])::text[])))
);

CREATE TABLE public.scheduled_agent_runs (
    id character varying(64) NOT NULL,
    job_id character varying(64) NOT NULL,
    request_id character varying(64) NOT NULL,
    thread_id character varying(64) NOT NULL,
    trigger character varying(16) DEFAULT 'scheduled'::character varying NOT NULL,
    occurrence_key character varying(128) NOT NULL,
    scheduled_for timestamp without time zone NOT NULL,
    project_id character varying(64) NOT NULL,
    agent_slug character varying(64) NOT NULL,
    conversation_title character varying(255) NOT NULL,
    prompt text NOT NULL,
    tool_approval_mode character varying(32) NOT NULL,
    model_spec character varying(512),
    status character varying(32) DEFAULT 'dispatching'::character varying NOT NULL,
    error_message text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.skills (
    id integer NOT NULL,
    slug character varying(128) NOT NULL,
    name character varying(128) NOT NULL,
    description text NOT NULL,
    source_type character varying(32) NOT NULL,
    tool_dependencies json NOT NULL,
    mcp_dependencies json NOT NULL,
    skill_dependencies json NOT NULL,
    dir_path character varying(512) NOT NULL,
    version character varying(64),
    content_hash character varying(128),
    share_config jsonb NOT NULL,
    enabled boolean NOT NULL,
    created_by character varying(64),
    updated_by character varying(64),
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.skills_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.skills_id_seq OWNED BY public.skills.id;

CREATE TABLE public.subagent_threads (
    id integer NOT NULL,
    uid character varying(64) NOT NULL,
    parent_conversation_id integer NOT NULL,
    child_conversation_id integer NOT NULL,
    child_thread_id character varying(64) NOT NULL,
    subagent_slug character varying(64) NOT NULL,
    created_by_run_id character varying(64) NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.subagent_threads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.subagent_threads_id_seq OWNED BY public.subagent_threads.id;

CREATE TABLE public.tasks (
    id character varying(32) NOT NULL,
    name character varying(255) NOT NULL,
    type character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    progress double precision NOT NULL,
    message text NOT NULL,
    payload json,
    result json,
    error text,
    cancel_requested integer NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    handler_version integer DEFAULT 1 NOT NULL,
    dedupe_key character varying(64),
    attempt_count integer DEFAULT 0 NOT NULL,
    worker_id character varying(128),
    heartbeat_at timestamp without time zone,
    lease_expires_at timestamp without time zone,
    timeout_seconds double precision DEFAULT 21600.0 NOT NULL
);

CREATE TABLE public.tool_calls (
    id integer NOT NULL,
    message_id integer NOT NULL,
    langgraph_tool_call_id character varying(100),
    tool_name character varying(100) NOT NULL,
    tool_input json,
    tool_output text,
    status character varying(20),
    error_message text,
    created_at timestamp without time zone
);

CREATE SEQUENCE public.tool_calls_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.tool_calls_id_seq OWNED BY public.tool_calls.id;

CREATE TABLE public.user_config (
    id integer NOT NULL,
    uid character varying NOT NULL,
    enable_memory boolean NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);

CREATE SEQUENCE public.user_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.user_config_id_seq OWNED BY public.user_config.id;

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying NOT NULL,
    uid character varying NOT NULL,
    phone_number character varying,
    avatar character varying,
    password_hash character varying NOT NULL,
    role character varying NOT NULL,
    department_id integer,
    created_at timestamp without time zone,
    last_login timestamp without time zone,
    login_failed_count integer NOT NULL,
    last_failed_login timestamp without time zone,
    login_locked_until timestamp without time zone,
    is_deleted integer NOT NULL,
    deleted_at timestamp without time zone
);

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;

CREATE TABLE public.wps_cloud_config (
    id integer NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    app_id character varying(255) DEFAULT ''::character varying NOT NULL,
    app_secret_encrypted text DEFAULT ''::text NOT NULL,
    redirect_uri character varying(2048) DEFAULT ''::character varying NOT NULL,
    version character varying(32) DEFAULT ''::character varying NOT NULL,
    updated_at timestamp without time zone,
    CONSTRAINT ck_wps_cloud_config_singleton CHECK ((id = 1))
);

CREATE SEQUENCE public.wps_cloud_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.wps_cloud_config_id_seq OWNED BY public.wps_cloud_config.id;

CREATE TABLE public.wps_connections (
    uid character varying NOT NULL,
    wps_user_id character varying(255),
    wps_user_name character varying(255),
    company_id character varying(255),
    access_token_encrypted text,
    refresh_token_encrypted text,
    expires_at timestamp without time zone,
    refresh_expires_at timestamp without time zone,
    config_version character varying(32),
    oauth_state_hash character varying(64),
    oauth_expires_at timestamp without time zone,
    oauth_config_version character varying(32),
    connected_at timestamp without time zone,
    updated_at timestamp without time zone,
    oauth_redirect_uri character varying(2048),
    oauth_tunnel_id character varying(64)
);

CREATE TABLE public.yuxi_schema_migrations (
    domain character varying(32) NOT NULL,
    version integer NOT NULL,
    applied_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT yuxi_schema_migrations_version_check CHECK ((version > 0))
);

ALTER TABLE ONLY public.agent_envs ALTER COLUMN id SET DEFAULT nextval('public.agent_envs_id_seq'::regclass);

ALTER TABLE ONLY public.agent_run_attempts ALTER COLUMN id SET DEFAULT nextval('public.agent_run_attempts_id_seq'::regclass);

ALTER TABLE ONLY public.agent_run_requests ALTER COLUMN id SET DEFAULT nextval('public.agent_run_requests_id_seq'::regclass);

ALTER TABLE ONLY public.agents ALTER COLUMN id SET DEFAULT nextval('public.agents_id_seq'::regclass);

ALTER TABLE ONLY public.api_keys ALTER COLUMN id SET DEFAULT nextval('public.api_keys_id_seq'::regclass);

ALTER TABLE ONLY public.cli_auth_sessions ALTER COLUMN id SET DEFAULT nextval('public.cli_auth_sessions_id_seq'::regclass);

ALTER TABLE ONLY public.config_options ALTER COLUMN id SET DEFAULT nextval('public.config_options_id_seq'::regclass);

ALTER TABLE ONLY public.conversation_stats ALTER COLUMN id SET DEFAULT nextval('public.conversation_stats_id_seq'::regclass);

ALTER TABLE ONLY public.conversations ALTER COLUMN id SET DEFAULT nextval('public.conversations_id_seq'::regclass);

ALTER TABLE ONLY public.departments ALTER COLUMN id SET DEFAULT nextval('public.departments_id_seq'::regclass);

ALTER TABLE ONLY public.evaluation_dataset_items ALTER COLUMN id SET DEFAULT nextval('public.evaluation_dataset_items_id_seq'::regclass);

ALTER TABLE ONLY public.evaluation_datasets ALTER COLUMN id SET DEFAULT nextval('public.evaluation_datasets_id_seq'::regclass);

ALTER TABLE ONLY public.evaluation_run_items ALTER COLUMN id SET DEFAULT nextval('public.evaluation_run_items_id_seq'::regclass);

ALTER TABLE ONLY public.evaluation_runs ALTER COLUMN id SET DEFAULT nextval('public.evaluation_runs_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_bases ALTER COLUMN id SET DEFAULT nextval('public.knowledge_bases_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_chunks ALTER COLUMN id SET DEFAULT nextval('public.knowledge_chunks_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_files ALTER COLUMN id SET DEFAULT nextval('public.knowledge_files_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_graph_entities ALTER COLUMN id SET DEFAULT nextval('public.knowledge_graph_entities_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_graph_entity_mentions ALTER COLUMN id SET DEFAULT nextval('public.knowledge_graph_entity_mentions_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_graph_triple_mentions ALTER COLUMN id SET DEFAULT nextval('public.knowledge_graph_triple_mentions_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_graph_triples ALTER COLUMN id SET DEFAULT nextval('public.knowledge_graph_triples_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions ALTER COLUMN id SET DEFAULT nextval('public.knowledge_parent_graph_entity_mentions_id_seq'::regclass);

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions ALTER COLUMN id SET DEFAULT nextval('public.knowledge_parent_graph_triple_mentions_id_seq'::regclass);

ALTER TABLE ONLY public.mcp_servers ALTER COLUMN id SET DEFAULT nextval('public.mcp_servers_id_seq'::regclass);

ALTER TABLE ONLY public.message_feedbacks ALTER COLUMN id SET DEFAULT nextval('public.message_feedbacks_id_seq'::regclass);

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);

ALTER TABLE ONLY public.model_providers ALTER COLUMN id SET DEFAULT nextval('public.model_providers_id_seq'::regclass);

ALTER TABLE ONLY public.operation_logs ALTER COLUMN id SET DEFAULT nextval('public.operation_logs_id_seq'::regclass);

ALTER TABLE ONLY public.skills ALTER COLUMN id SET DEFAULT nextval('public.skills_id_seq'::regclass);

ALTER TABLE ONLY public.subagent_threads ALTER COLUMN id SET DEFAULT nextval('public.subagent_threads_id_seq'::regclass);

ALTER TABLE ONLY public.tool_calls ALTER COLUMN id SET DEFAULT nextval('public.tool_calls_id_seq'::regclass);

ALTER TABLE ONLY public.user_config ALTER COLUMN id SET DEFAULT nextval('public.user_config_id_seq'::regclass);

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);

ALTER TABLE ONLY public.wps_cloud_config ALTER COLUMN id SET DEFAULT nextval('public.wps_cloud_config_id_seq'::regclass);

ALTER TABLE ONLY public.agent_envs
    ADD CONSTRAINT agent_envs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_run_attempts
    ADD CONSTRAINT agent_run_attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_run_requests
    ADD CONSTRAINT agent_run_requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.checkpoint_blobs
    ADD CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version);

ALTER TABLE ONLY public.checkpoint_migrations
    ADD CONSTRAINT checkpoint_migrations_pkey PRIMARY KEY (v);

ALTER TABLE ONLY public.checkpoint_writes
    ADD CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx);

ALTER TABLE ONLY public.checkpoints
    ADD CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id);

ALTER TABLE ONLY public.cli_auth_sessions
    ADD CONSTRAINT cli_auth_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.config_options
    ADD CONSTRAINT config_options_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.conversation_stats
    ADD CONSTRAINT conversation_stats_conversation_id_key UNIQUE (conversation_id);

ALTER TABLE ONLY public.conversation_stats
    ADD CONSTRAINT conversation_stats_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.departments
    ADD CONSTRAINT departments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.evaluation_dataset_items
    ADD CONSTRAINT evaluation_dataset_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.evaluation_datasets
    ADD CONSTRAINT evaluation_datasets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.evaluation_run_items
    ADD CONSTRAINT evaluation_run_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT evaluation_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_bases
    ADD CONSTRAINT knowledge_bases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT knowledge_child_chunks_pkey PRIMARY KEY (child_id);

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_pkey PRIMARY KEY (version_id);

ALTER TABLE ONLY public.knowledge_files
    ADD CONSTRAINT knowledge_files_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_graph_entities
    ADD CONSTRAINT knowledge_graph_entities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT knowledge_graph_entity_mentions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT knowledge_graph_triple_mentions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_graph_triples
    ADD CONSTRAINT knowledge_graph_triples_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_parent_chunks
    ADD CONSTRAINT knowledge_parent_chunks_pkey PRIMARY KEY (parent_id);

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT knowledge_parent_graph_entity_mentions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT knowledge_parent_graph_triple_mentions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mcp_servers
    ADD CONSTRAINT mcp_servers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.message_feedbacks
    ADD CONSTRAINT message_feedbacks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_providers
    ADD CONSTRAINT model_providers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.operation_logs
    ADD CONSTRAINT operation_logs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.scheduled_agent_jobs
    ADD CONSTRAINT scheduled_agent_jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.scheduled_agent_runs
    ADD CONSTRAINT scheduled_agent_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.subagent_threads
    ADD CONSTRAINT subagent_threads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tool_calls
    ADD CONSTRAINT tool_calls_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_run_attempts
    ADD CONSTRAINT uq_agent_run_attempts_run_attempt_no UNIQUE (run_id, attempt_no);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT uq_conversations_uid_creation_request_id UNIQUE (uid, creation_request_id);

ALTER TABLE ONLY public.evaluation_dataset_items
    ADD CONSTRAINT uq_evaluation_dataset_items_dataset_index UNIQUE (dataset_id, item_index);

ALTER TABLE ONLY public.evaluation_dataset_items
    ADD CONSTRAINT uq_evaluation_dataset_items_item_id UNIQUE (item_id);

ALTER TABLE ONLY public.evaluation_datasets
    ADD CONSTRAINT uq_evaluation_datasets_dataset_id UNIQUE (dataset_id);

ALTER TABLE ONLY public.evaluation_run_items
    ADD CONSTRAINT uq_evaluation_run_items_run_index UNIQUE (run_id, item_index);

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT uq_evaluation_runs_run_id UNIQUE (run_id);

ALTER TABLE ONLY public.knowledge_bases
    ADD CONSTRAINT uq_knowledge_bases_kb_id UNIQUE (kb_id);

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT uq_knowledge_child_chunks_version_index UNIQUE (version_id, child_index);

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT uq_knowledge_chunks_chunk_id UNIQUE (chunk_id);

ALTER TABLE ONLY public.knowledge_files
    ADD CONSTRAINT uq_knowledge_files_file_id UNIQUE (file_id);

ALTER TABLE ONLY public.knowledge_graph_entities
    ADD CONSTRAINT uq_knowledge_graph_entities_entity_id UNIQUE (entity_id);

ALTER TABLE ONLY public.knowledge_graph_entities
    ADD CONSTRAINT uq_knowledge_graph_entities_identity UNIQUE (kb_id, normalized_name, label);

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT uq_knowledge_graph_entity_mentions_entity_chunk UNIQUE (entity_id, chunk_id);

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT uq_knowledge_graph_triple_mentions_triple_chunk UNIQUE (triple_id, chunk_id);

ALTER TABLE ONLY public.knowledge_graph_triples
    ADD CONSTRAINT uq_knowledge_graph_triples_triple_id UNIQUE (triple_id);

ALTER TABLE ONLY public.knowledge_parent_chunks
    ADD CONSTRAINT uq_knowledge_parent_chunks_version_index UNIQUE (version_id, parent_index);

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT uq_knowledge_parent_graph_entity_mentions_entity_parent UNIQUE (entity_id, parent_id);

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT uq_knowledge_parent_graph_triple_mentions_triple_parent UNIQUE (triple_id, parent_id);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT uq_projects_id_uid UNIQUE (id, uid);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT uq_projects_uid_idempotency_key UNIQUE (uid, idempotency_key);

ALTER TABLE ONLY public.scheduled_agent_jobs
    ADD CONSTRAINT uq_scheduled_agent_jobs_uid_creation_request UNIQUE (uid, creation_request_id);

ALTER TABLE ONLY public.scheduled_agent_runs
    ADD CONSTRAINT uq_scheduled_agent_runs_job_occurrence UNIQUE (job_id, occurrence_key);

ALTER TABLE ONLY public.scheduled_agent_runs
    ADD CONSTRAINT uq_scheduled_agent_runs_request UNIQUE (request_id);

ALTER TABLE ONLY public.scheduled_agent_runs
    ADD CONSTRAINT uq_scheduled_agent_runs_thread UNIQUE (thread_id);

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT uq_tasks_active_dedupe UNIQUE (type, dedupe_key);

ALTER TABLE ONLY public.user_config
    ADD CONSTRAINT user_config_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.wps_cloud_config
    ADD CONSTRAINT wps_cloud_config_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.wps_connections
    ADD CONSTRAINT wps_connections_pkey PRIMARY KEY (uid);

ALTER TABLE ONLY public.yuxi_schema_migrations
    ADD CONSTRAINT yuxi_schema_migrations_pkey PRIMARY KEY (domain);

CREATE INDEX checkpoint_blobs_thread_id_idx ON public.checkpoint_blobs USING btree (thread_id);

CREATE INDEX checkpoint_writes_thread_id_idx ON public.checkpoint_writes USING btree (thread_id);

CREATE INDEX checkpoints_thread_id_idx ON public.checkpoints USING btree (thread_id);

CREATE INDEX idx_agent_runs_conversation_thread_created ON public.agent_runs USING btree (conversation_thread_id, created_at DESC);

CREATE INDEX idx_agent_runs_created_by_run_created ON public.agent_runs USING btree (created_by_run_id, created_at DESC);

CREATE INDEX idx_agent_runs_status_updated ON public.agent_runs USING btree (status, updated_at);

CREATE INDEX idx_agent_runs_subagent_lookup ON public.agent_runs USING btree (uid, conversation_thread_id, run_type, created_at DESC);

CREATE INDEX idx_agent_runs_uid_created ON public.agent_runs USING btree (uid, created_at DESC);

CREATE INDEX idx_kb_name ON public.knowledge_bases USING btree (name);

CREATE INDEX idx_kb_type ON public.knowledge_bases USING btree (kb_type);

CREATE INDEX idx_kf_hash ON public.knowledge_files USING btree (content_hash);

CREATE INDEX idx_kf_kb_filename ON public.knowledge_files USING btree (kb_id, filename);

CREATE INDEX idx_kf_kb_id ON public.knowledge_files USING btree (kb_id);

CREATE INDEX idx_kf_kb_parent_flat ON public.knowledge_files USING btree (kb_id, parent_id) WHERE ((strpos((filename)::text, '/'::text) = 0) AND (filename IS NOT NULL) AND ((filename)::text <> ''::text));

CREATE INDEX idx_kf_kb_parent_segment ON public.knowledge_files USING btree (kb_id, parent_id, split_part((filename)::text, '/'::text, 1)) WHERE (strpos((filename)::text, '/'::text) > 0);

CREATE INDEX idx_kf_parent ON public.knowledge_files USING btree (parent_id);

CREATE INDEX idx_kf_status ON public.knowledge_files USING btree (status);

CREATE UNIQUE INDEX ix_agent_envs_uid ON public.agent_envs USING btree (uid);

CREATE INDEX ix_agent_run_attempts_open ON public.agent_run_attempts USING btree (run_id, finished_at);

CREATE INDEX ix_agent_run_requests_dispatched_run_id ON public.agent_run_requests USING btree (dispatched_run_id);

CREATE INDEX ix_agent_run_requests_external_id ON public.agent_run_requests USING btree (external_id);

CREATE INDEX ix_agent_run_requests_queue ON public.agent_run_requests USING btree (uid, agent_slug, conversation_thread_id, status, created_at, id);

CREATE UNIQUE INDEX ix_agent_run_requests_request_id ON public.agent_run_requests USING btree (request_id);

CREATE INDEX ix_agent_runs_agent_slug ON public.agent_runs USING btree (agent_slug);

CREATE INDEX ix_agent_runs_conversation_id ON public.agent_runs USING btree (conversation_id);

CREATE INDEX ix_agent_runs_conversation_thread_id ON public.agent_runs USING btree (conversation_thread_id);

CREATE INDEX ix_agent_runs_created_by_run_id ON public.agent_runs USING btree (created_by_run_id);

CREATE INDEX ix_agent_runs_external_id ON public.agent_runs USING btree (external_id);

CREATE UNIQUE INDEX ix_agent_runs_request_id ON public.agent_runs USING btree (request_id);

CREATE INDEX ix_agent_runs_runtime_cleanup_pending ON public.agent_runs USING btree (runtime_cleanup_pending);

CREATE INDEX ix_agent_runs_runtime_scope_id ON public.agent_runs USING btree (runtime_scope_id);

CREATE INDEX ix_agent_runs_status ON public.agent_runs USING btree (status);

CREATE INDEX ix_agent_runs_status_lease_expires ON public.agent_runs USING btree (status, lease_expires_at);

CREATE INDEX ix_agent_runs_subagent_thread_relation_id ON public.agent_runs USING btree (subagent_thread_relation_id);

CREATE INDEX ix_agent_runs_uid ON public.agent_runs USING btree (uid);

CREATE INDEX ix_agents_backend_id ON public.agents USING btree (backend_id);

CREATE INDEX ix_agents_created_by ON public.agents USING btree (created_by);

CREATE INDEX ix_agents_is_default ON public.agents USING btree (is_default);

CREATE INDEX ix_agents_is_subagent ON public.agents USING btree (is_subagent);

CREATE UNIQUE INDEX ix_agents_slug ON public.agents USING btree (slug);

CREATE INDEX ix_api_keys_department_id ON public.api_keys USING btree (department_id);

CREATE UNIQUE INDEX ix_api_keys_key_hash ON public.api_keys USING btree (key_hash);

CREATE UNIQUE INDEX ix_api_keys_request_id ON public.api_keys USING btree (request_id);

CREATE INDEX ix_api_keys_revoked_at ON public.api_keys USING btree (revoked_at);

CREATE INDEX ix_api_keys_user_id ON public.api_keys USING btree (user_id);

CREATE INDEX ix_cli_auth_sessions_api_key_id ON public.cli_auth_sessions USING btree (api_key_id);

CREATE INDEX ix_cli_auth_sessions_approved_user_id ON public.cli_auth_sessions USING btree (approved_user_id);

CREATE UNIQUE INDEX ix_cli_auth_sessions_device_code_hash ON public.cli_auth_sessions USING btree (device_code_hash);

CREATE INDEX ix_cli_auth_sessions_status ON public.cli_auth_sessions USING btree (status);

CREATE UNIQUE INDEX ix_cli_auth_sessions_user_code ON public.cli_auth_sessions USING btree (user_code);

CREATE UNIQUE INDEX ix_config_options_key ON public.config_options USING btree (key);

CREATE INDEX ix_conversations_agent_id ON public.conversations USING btree (agent_id);

CREATE INDEX ix_conversations_is_pinned ON public.conversations USING btree (is_pinned);

CREATE INDEX ix_conversations_project_id ON public.conversations USING btree (project_id);

CREATE UNIQUE INDEX ix_conversations_thread_id ON public.conversations USING btree (thread_id);

CREATE INDEX ix_conversations_uid ON public.conversations USING btree (uid);

CREATE UNIQUE INDEX ix_departments_name ON public.departments USING btree (name);

CREATE INDEX ix_evaluation_dataset_items_dataset_id ON public.evaluation_dataset_items USING btree (dataset_id);

CREATE INDEX ix_evaluation_dataset_items_dataset_index ON public.evaluation_dataset_items USING btree (dataset_id, item_index);

CREATE UNIQUE INDEX ix_evaluation_dataset_items_item_id ON public.evaluation_dataset_items USING btree (item_id);

CREATE INDEX ix_evaluation_dataset_items_kb_id ON public.evaluation_dataset_items USING btree (kb_id);

CREATE UNIQUE INDEX ix_evaluation_datasets_dataset_id ON public.evaluation_datasets USING btree (dataset_id);

CREATE INDEX ix_evaluation_datasets_kb_id ON public.evaluation_datasets USING btree (kb_id);

CREATE INDEX ix_evaluation_run_items_dataset_item_id ON public.evaluation_run_items USING btree (dataset_item_id);

CREATE INDEX ix_evaluation_run_items_run_id ON public.evaluation_run_items USING btree (run_id);

CREATE INDEX ix_evaluation_run_items_run_index ON public.evaluation_run_items USING btree (run_id, item_index);

CREATE INDEX ix_evaluation_runs_dataset_id ON public.evaluation_runs USING btree (dataset_id);

CREATE INDEX ix_evaluation_runs_kb_id ON public.evaluation_runs USING btree (kb_id);

CREATE UNIQUE INDEX ix_evaluation_runs_run_id ON public.evaluation_runs USING btree (run_id);

CREATE INDEX ix_evaluation_runs_started ON public.evaluation_runs USING btree (started_at DESC);

CREATE INDEX ix_evaluation_runs_started_at ON public.evaluation_runs USING btree (started_at);

CREATE INDEX ix_evaluation_runs_status ON public.evaluation_runs USING btree (status);

CREATE UNIQUE INDEX ix_knowledge_bases_kb_id ON public.knowledge_bases USING btree (kb_id);

CREATE INDEX ix_knowledge_bases_kb_type ON public.knowledge_bases USING btree (kb_type);

CREATE INDEX ix_knowledge_bases_name ON public.knowledge_bases USING btree (name);

CREATE INDEX ix_knowledge_child_chunks_file_id ON public.knowledge_child_chunks USING btree (file_id);

CREATE INDEX ix_knowledge_child_chunks_kb_id ON public.knowledge_child_chunks USING btree (kb_id);

CREATE INDEX ix_knowledge_child_chunks_parent_id ON public.knowledge_child_chunks USING btree (parent_id);

CREATE INDEX ix_knowledge_chunks_file_id ON public.knowledge_chunks USING btree (file_id);

CREATE INDEX ix_knowledge_chunks_graph_extraction_status ON public.knowledge_chunks USING btree (kb_id, ((graph_extraction_details ->> 'status'::text)));

CREATE INDEX ix_knowledge_chunks_graph_indexed ON public.knowledge_chunks USING btree (graph_indexed);

CREATE INDEX ix_knowledge_chunks_graph_structure_indexed ON public.knowledge_chunks USING btree (graph_structure_indexed);

CREATE INDEX ix_knowledge_chunks_kb_id ON public.knowledge_chunks USING btree (kb_id);

CREATE INDEX ix_knowledge_document_versions_kb_file_status ON public.knowledge_document_versions USING btree (kb_id, file_id, status);

CREATE INDEX ix_knowledge_files_content_hash ON public.knowledge_files USING btree (content_hash);

CREATE UNIQUE INDEX ix_knowledge_files_file_id ON public.knowledge_files USING btree (file_id);

CREATE INDEX ix_knowledge_files_kb_id ON public.knowledge_files USING btree (kb_id);

CREATE INDEX ix_knowledge_files_parent_id ON public.knowledge_files USING btree (parent_id);

CREATE INDEX ix_knowledge_files_processing_task_id ON public.knowledge_files USING btree (processing_task_id);

CREATE INDEX ix_knowledge_files_status ON public.knowledge_files USING btree (status);

CREATE INDEX ix_knowledge_graph_entities_kb_id ON public.knowledge_graph_entities USING btree (kb_id);

CREATE INDEX ix_knowledge_graph_entities_vector_pending ON public.knowledge_graph_entities USING btree (kb_id, vector_status, vector_next_retry_at);

CREATE INDEX ix_knowledge_graph_entity_mentions_chunk_id ON public.knowledge_graph_entity_mentions USING btree (chunk_id);

CREATE INDEX ix_knowledge_graph_entity_mentions_file_id ON public.knowledge_graph_entity_mentions USING btree (file_id);

CREATE INDEX ix_knowledge_graph_entity_mentions_kb_id ON public.knowledge_graph_entity_mentions USING btree (kb_id);

CREATE INDEX ix_knowledge_graph_triple_mentions_chunk_id ON public.knowledge_graph_triple_mentions USING btree (chunk_id);

CREATE INDEX ix_knowledge_graph_triple_mentions_file_id ON public.knowledge_graph_triple_mentions USING btree (file_id);

CREATE INDEX ix_knowledge_graph_triple_mentions_kb_id ON public.knowledge_graph_triple_mentions USING btree (kb_id);

CREATE INDEX ix_knowledge_graph_triples_kb_id ON public.knowledge_graph_triples USING btree (kb_id);

CREATE INDEX ix_knowledge_graph_triples_vector_pending ON public.knowledge_graph_triples USING btree (kb_id, vector_status, vector_next_retry_at);

CREATE INDEX ix_knowledge_parent_graph_entity_mentions_file_id ON public.knowledge_parent_graph_entity_mentions USING btree (file_id);

CREATE INDEX ix_knowledge_parent_graph_entity_mentions_kb_id ON public.knowledge_parent_graph_entity_mentions USING btree (kb_id);

CREATE INDEX ix_knowledge_parent_graph_entity_mentions_parent_id ON public.knowledge_parent_graph_entity_mentions USING btree (parent_id);

CREATE INDEX ix_knowledge_parent_graph_triple_mentions_file_id ON public.knowledge_parent_graph_triple_mentions USING btree (file_id);

CREATE INDEX ix_knowledge_parent_graph_triple_mentions_kb_id ON public.knowledge_parent_graph_triple_mentions USING btree (kb_id);

CREATE INDEX ix_knowledge_parent_graph_triple_mentions_parent_id ON public.knowledge_parent_graph_triple_mentions USING btree (parent_id);

CREATE UNIQUE INDEX ix_mcp_servers_slug ON public.mcp_servers USING btree (slug);

CREATE INDEX ix_message_feedbacks_message_id ON public.message_feedbacks USING btree (message_id);

CREATE INDEX ix_message_feedbacks_uid ON public.message_feedbacks USING btree (uid);

CREATE INDEX ix_messages_conversation_id ON public.messages USING btree (conversation_id);

CREATE INDEX ix_messages_request_id ON public.messages USING btree (request_id);

CREATE INDEX ix_messages_run_id ON public.messages USING btree (run_id);

CREATE INDEX ix_messages_run_sequence ON public.messages USING btree (run_id, sequence) WHERE (sequence IS NOT NULL);

CREATE INDEX ix_model_providers_is_enabled ON public.model_providers USING btree (is_enabled);

CREATE UNIQUE INDEX ix_model_providers_provider_id ON public.model_providers USING btree (provider_id);

CREATE INDEX ix_projects_selection_status ON public.projects USING btree (selection_status);

CREATE INDEX ix_projects_status ON public.projects USING btree (status);

CREATE INDEX ix_projects_uid ON public.projects USING btree (uid);

CREATE INDEX ix_scheduled_agent_jobs_deleted_at ON public.scheduled_agent_jobs USING btree (deleted_at);

CREATE INDEX ix_scheduled_agent_jobs_due ON public.scheduled_agent_jobs USING btree (enabled, next_run_at);

CREATE INDEX ix_scheduled_agent_jobs_project_id ON public.scheduled_agent_jobs USING btree (project_id);

CREATE INDEX ix_scheduled_agent_jobs_uid ON public.scheduled_agent_jobs USING btree (uid);

CREATE INDEX ix_scheduled_agent_runs_dispatching ON public.scheduled_agent_runs USING btree (status, created_at);

CREATE INDEX ix_scheduled_agent_runs_job_created ON public.scheduled_agent_runs USING btree (job_id, created_at);

CREATE UNIQUE INDEX ix_skills_slug ON public.skills USING btree (slug);

CREATE INDEX ix_skills_source_type ON public.skills USING btree (source_type);

CREATE UNIQUE INDEX ix_subagent_threads_child_conversation_id ON public.subagent_threads USING btree (child_conversation_id);

CREATE UNIQUE INDEX ix_subagent_threads_child_thread_id ON public.subagent_threads USING btree (child_thread_id);

CREATE INDEX ix_subagent_threads_created_by_run_id ON public.subagent_threads USING btree (created_by_run_id);

CREATE INDEX ix_subagent_threads_parent_conversation ON public.subagent_threads USING btree (parent_conversation_id);

CREATE INDEX ix_subagent_threads_parent_conversation_id ON public.subagent_threads USING btree (parent_conversation_id);

CREATE INDEX ix_subagent_threads_subagent_slug ON public.subagent_threads USING btree (subagent_slug);

CREATE INDEX ix_subagent_threads_uid ON public.subagent_threads USING btree (uid);

CREATE INDEX ix_tasks_created_at ON public.tasks USING btree (created_at);

CREATE INDEX ix_tasks_status ON public.tasks USING btree (status);

CREATE INDEX ix_tasks_status_lease_expires ON public.tasks USING btree (status, lease_expires_at);

CREATE INDEX ix_tasks_type ON public.tasks USING btree (type);

CREATE INDEX ix_tool_calls_langgraph_tool_call_id ON public.tool_calls USING btree (langgraph_tool_call_id);

CREATE INDEX ix_tool_calls_message_id ON public.tool_calls USING btree (message_id);

CREATE UNIQUE INDEX ix_user_config_uid ON public.user_config USING btree (uid);

CREATE INDEX ix_users_is_deleted ON public.users USING btree (is_deleted);

CREATE UNIQUE INDEX ix_users_phone_number ON public.users USING btree (phone_number);

CREATE UNIQUE INDEX ix_users_uid ON public.users USING btree (uid);

CREATE UNIQUE INDEX ix_users_username ON public.users USING btree (username);

CREATE UNIQUE INDEX uq_agent_runs_one_active_per_thread ON public.agent_runs USING btree (uid, agent_slug, conversation_thread_id) WHERE ((status)::text <> ALL ((ARRAY['completed'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'interrupted'::character varying])::text[]));

CREATE UNIQUE INDEX uq_agents_default ON public.agents USING btree (is_default) WHERE (is_default IS TRUE);

CREATE UNIQUE INDEX uq_knowledge_document_versions_active_file ON public.knowledge_document_versions USING btree (kb_id, file_id) WHERE ((status)::text = 'active'::text);

CREATE UNIQUE INDEX uq_messages_run_role_operation_id ON public.messages USING btree (run_id, role, operation_id) WHERE (operation_id IS NOT NULL);

ALTER TABLE ONLY public.agent_envs
    ADD CONSTRAINT agent_envs_uid_fkey FOREIGN KEY (uid) REFERENCES public.users(uid);

ALTER TABLE ONLY public.agent_run_attempts
    ADD CONSTRAINT agent_run_attempts_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.agent_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_run_requests
    ADD CONSTRAINT agent_run_requests_dispatched_run_id_fkey FOREIGN KEY (dispatched_run_id) REFERENCES public.agent_runs(id);

ALTER TABLE ONLY public.agent_run_requests
    ADD CONSTRAINT agent_run_requests_input_message_id_fkey FOREIGN KEY (input_message_id) REFERENCES public.messages(id);

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_subagent_thread_relation_id_fkey FOREIGN KEY (subagent_thread_relation_id) REFERENCES public.subagent_threads(id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.cli_auth_sessions
    ADD CONSTRAINT cli_auth_sessions_api_key_id_fkey FOREIGN KEY (api_key_id) REFERENCES public.api_keys(id);

ALTER TABLE ONLY public.cli_auth_sessions
    ADD CONSTRAINT cli_auth_sessions_approved_user_id_fkey FOREIGN KEY (approved_user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.conversation_stats
    ADD CONSTRAINT conversation_stats_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);

ALTER TABLE ONLY public.evaluation_dataset_items
    ADD CONSTRAINT evaluation_dataset_items_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.evaluation_datasets(dataset_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.evaluation_dataset_items
    ADD CONSTRAINT evaluation_dataset_items_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.evaluation_datasets
    ADD CONSTRAINT evaluation_datasets_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.evaluation_run_items
    ADD CONSTRAINT evaluation_run_items_dataset_item_id_fkey FOREIGN KEY (dataset_item_id) REFERENCES public.evaluation_dataset_items(item_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.evaluation_run_items
    ADD CONSTRAINT evaluation_run_items_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.evaluation_runs(run_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT evaluation_runs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.evaluation_datasets(dataset_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT evaluation_runs_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT fk_conversations_project_uid FOREIGN KEY (project_id, uid) REFERENCES public.projects(id, uid);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_uid_users FOREIGN KEY (uid) REFERENCES public.users(uid) ON DELETE CASCADE;

ALTER TABLE ONLY public.scheduled_agent_jobs
    ADD CONSTRAINT fk_scheduled_agent_jobs_project_uid FOREIGN KEY (project_id, uid) REFERENCES public.projects(id, uid) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT knowledge_child_chunks_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT knowledge_child_chunks_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT knowledge_child_chunks_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.knowledge_parent_chunks(parent_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_child_chunks
    ADD CONSTRAINT knowledge_child_chunks_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.knowledge_document_versions(version_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_files
    ADD CONSTRAINT knowledge_files_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_files
    ADD CONSTRAINT knowledge_files_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.knowledge_files(file_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.knowledge_graph_entities
    ADD CONSTRAINT knowledge_graph_entities_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT knowledge_graph_entity_mentions_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.knowledge_chunks(chunk_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT knowledge_graph_entity_mentions_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.knowledge_graph_entities(entity_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT knowledge_graph_entity_mentions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_entity_mentions
    ADD CONSTRAINT knowledge_graph_entity_mentions_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT knowledge_graph_triple_mentions_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.knowledge_chunks(chunk_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT knowledge_graph_triple_mentions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT knowledge_graph_triple_mentions_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triple_mentions
    ADD CONSTRAINT knowledge_graph_triple_mentions_triple_id_fkey FOREIGN KEY (triple_id) REFERENCES public.knowledge_graph_triples(triple_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triples
    ADD CONSTRAINT knowledge_graph_triples_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triples
    ADD CONSTRAINT knowledge_graph_triples_source_entity_id_fkey FOREIGN KEY (source_entity_id) REFERENCES public.knowledge_graph_entities(entity_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_graph_triples
    ADD CONSTRAINT knowledge_graph_triples_target_entity_id_fkey FOREIGN KEY (target_entity_id) REFERENCES public.knowledge_graph_entities(entity_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_chunks
    ADD CONSTRAINT knowledge_parent_chunks_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_chunks
    ADD CONSTRAINT knowledge_parent_chunks_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_chunks
    ADD CONSTRAINT knowledge_parent_chunks_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.knowledge_document_versions(version_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT knowledge_parent_graph_entity_mentions_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.knowledge_graph_entities(entity_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT knowledge_parent_graph_entity_mentions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT knowledge_parent_graph_entity_mentions_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_entity_mentions
    ADD CONSTRAINT knowledge_parent_graph_entity_mentions_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.knowledge_parent_chunks(parent_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT knowledge_parent_graph_triple_mentions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.knowledge_files(file_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT knowledge_parent_graph_triple_mentions_kb_id_fkey FOREIGN KEY (kb_id) REFERENCES public.knowledge_bases(kb_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT knowledge_parent_graph_triple_mentions_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.knowledge_parent_chunks(parent_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.knowledge_parent_graph_triple_mentions
    ADD CONSTRAINT knowledge_parent_graph_triple_mentions_triple_id_fkey FOREIGN KEY (triple_id) REFERENCES public.knowledge_graph_triples(triple_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.message_feedbacks
    ADD CONSTRAINT message_feedbacks_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.agent_runs(id);

ALTER TABLE ONLY public.operation_logs
    ADD CONSTRAINT operation_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.scheduled_agent_jobs
    ADD CONSTRAINT scheduled_agent_jobs_uid_fkey FOREIGN KEY (uid) REFERENCES public.users(uid) ON DELETE CASCADE;

ALTER TABLE ONLY public.scheduled_agent_runs
    ADD CONSTRAINT scheduled_agent_runs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.scheduled_agent_jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.subagent_threads
    ADD CONSTRAINT subagent_threads_child_conversation_id_fkey FOREIGN KEY (child_conversation_id) REFERENCES public.conversations(id);

ALTER TABLE ONLY public.subagent_threads
    ADD CONSTRAINT subagent_threads_parent_conversation_id_fkey FOREIGN KEY (parent_conversation_id) REFERENCES public.conversations(id);

ALTER TABLE ONLY public.tool_calls
    ADD CONSTRAINT tool_calls_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id);

ALTER TABLE ONLY public.user_config
    ADD CONSTRAINT user_config_uid_fkey FOREIGN KEY (uid) REFERENCES public.users(uid);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id);

ALTER TABLE ONLY public.wps_connections
    ADD CONSTRAINT wps_connections_uid_fkey FOREIGN KEY (uid) REFERENCES public.users(uid) ON DELETE CASCADE;
