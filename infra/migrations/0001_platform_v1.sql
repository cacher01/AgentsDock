-- Platform schema v1.
-- Applied transactionally by scripts/postgres_migrate.py.
-- IDs are text to preserve prefixed legacy identifiers during SQLite migration.

-- Go-owned identity and workspace tables.
create table users (
    id text primary key,
    email text not null,
    display_name text not null,
    password_hash text not null,
    status text not null default 'active'
        check (status in ('active', 'disabled')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index uq_users_email_lower on users (lower(email));

create table workspaces (
    id text primary key,
    name text not null,
    slug text not null,
    created_by text not null references users(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index uq_workspaces_slug_lower on workspaces (lower(slug));

create table workspace_memberships (
    workspace_id text not null references workspaces(id) on delete cascade,
    user_id text not null references users(id) on delete cascade,
    role text not null check (role in ('admin', 'member')),
    status text not null default 'active'
        check (status in ('invited', 'active', 'disabled')),
    joined_at timestamptz,
    created_at timestamptz not null default now(),
    primary key (workspace_id, user_id)
);

create index idx_workspace_memberships_user
    on workspace_memberships (user_id, status);

create table invitations (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    email text not null,
    role text not null check (role in ('admin', 'member')),
    token_hash text not null unique,
    expires_at timestamptz not null,
    accepted_at timestamptz,
    created_by text not null references users(id),
    created_at timestamptz not null default now()
);

create index idx_invitations_workspace_email
    on invitations (workspace_id, lower(email));

create table auth_sessions (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    token_hash text not null unique,
    expires_at timestamptz not null,
    revoked_at timestamptz,
    last_seen_at timestamptz,
    created_at timestamptz not null default now()
);

create index idx_auth_sessions_user_active
    on auth_sessions (user_id, expires_at)
    where revoked_at is null;

-- Go-owned model catalog and policy tables.
create table model_connections (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    name text not null,
    provider_type text not null,
    base_url text not null,
    secret_ciphertext bytea not null,
    secret_nonce bytea not null,
    secret_key_version integer not null,
    status text not null default 'active'
        check (status in ('active', 'disabled', 'unhealthy')),
    created_by text not null references users(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, name)
);

create table model_variants (
    id text primary key,
    connection_id text not null references model_connections(id) on delete cascade,
    model_name text not null,
    capabilities jsonb not null default '[]'::jsonb,
    context_window integer check (context_window is null or context_window > 0),
    pricing jsonb not null default '{}'::jsonb,
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (connection_id, model_name)
);

create table model_fallback_groups (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    name text not null,
    created_at timestamptz not null default now(),
    unique (workspace_id, name)
);

create table model_fallback_members (
    group_id text not null references model_fallback_groups(id) on delete cascade,
    model_variant_id text not null references model_variants(id) on delete cascade,
    priority integer not null check (priority >= 1),
    primary key (group_id, model_variant_id),
    unique (group_id, priority)
);

-- Python owns definitions; Go owns workspace bindings and credential references.
create table tool_definitions (
    id text primary key,
    tool_key text not null,
    version text not null,
    adapter_type text not null,
    input_schema jsonb not null,
    output_schema jsonb not null,
    risk_level text not null
        check (risk_level in ('low', 'medium', 'high')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (tool_key, version)
);

create table workspace_tool_bindings (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    tool_definition_id text not null references tool_definitions(id),
    credential_ref text,
    enabled boolean not null default false,
    policy jsonb not null default '{}'::jsonb,
    created_by text not null references users(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (workspace_id, tool_definition_id)
);

-- Python-owned task runtime tables.
create table task_sessions (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    owner_user_id text not null references users(id),
    title text not null,
    status text not null default 'active'
        check (status in ('active', 'archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, workspace_id, owner_user_id),
    foreign key (workspace_id, owner_user_id)
        references workspace_memberships(workspace_id, user_id)
);

create index idx_task_sessions_scope
    on task_sessions (workspace_id, owner_user_id, updated_at desc);

create table tasks (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    owner_user_id text not null references users(id),
    task_session_id text,
    input text not null,
    objective text not null default '',
    workflow text,
    status text not null
        check (status in (
            'created', 'queued', 'running', 'completed',
            'degraded', 'failed', 'cancelled'
        )),
    priority integer not null default 0,
    idempotency_key text,
    token_estimate bigint not null default 0 check (token_estimate >= 0),
    error_summary text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    unique (id, workspace_id, owner_user_id),
    foreign key (workspace_id, owner_user_id)
        references workspace_memberships(workspace_id, user_id),
    foreign key (task_session_id, workspace_id, owner_user_id)
        references task_sessions(id, workspace_id, owner_user_id)
        on delete set null (task_session_id)
);

create unique index uq_tasks_idempotency
    on tasks (workspace_id, owner_user_id, idempotency_key)
    where idempotency_key is not null;

create index idx_tasks_scope_created
    on tasks (workspace_id, owner_user_id, created_at desc);

create index idx_tasks_queue
    on tasks (priority desc, created_at asc)
    where status in ('queued', 'running');

create table task_runs (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    attempt integer not null check (attempt >= 1),
    status text not null
        check (status in (
            'queued', 'running', 'completed',
            'degraded', 'failed', 'cancelled'
        )),
    worker_id text,
    started_at timestamptz,
    finished_at timestamptz,
    error_summary text not null default '',
    created_at timestamptz not null default now(),
    unique (task_id, attempt)
);

create table execution_nodes (
    id text primary key,
    run_id text not null references task_runs(id) on delete cascade,
    node_key text not null,
    node_type text not null,
    status text not null
        check (status in (
            'pending', 'running', 'completed', 'blocked',
            'failed', 'skipped', 'cancelled'
        )),
    attempt integer not null default 1 check (attempt >= 1),
    input_ref text,
    output_ref text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (run_id, node_key, attempt)
);

create index idx_execution_nodes_run_status
    on execution_nodes (run_id, status);

create table checkpoints (
    id text primary key,
    run_id text not null references task_runs(id) on delete cascade,
    sequence integer not null check (sequence >= 1),
    workflow_version text not null,
    phase text not null,
    state_json jsonb not null,
    created_at timestamptz not null default now(),
    unique (run_id, sequence)
);

create table worker_leases (
    task_id text primary key references tasks(id) on delete cascade,
    worker_id text not null,
    lease_token text not null unique,
    leased_until timestamptz not null,
    heartbeat_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create index idx_worker_leases_expiry
    on worker_leases (leased_until);

create table agent_runs (
    id text primary key,
    run_id text not null references task_runs(id) on delete cascade,
    parent_node_id text references execution_nodes(id) on delete set null,
    role text not null,
    goal text not null,
    capability_snapshot jsonb not null default '{}'::jsonb,
    status text not null
        check (status in (
            'created', 'running', 'completed', 'blocked', 'failed', 'cancelled'
        )),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index idx_agent_runs_run
    on agent_runs (run_id, created_at);

create table events (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    run_id text references task_runs(id) on delete cascade,
    agent_run_id text references agent_runs(id) on delete set null,
    sequence bigint not null check (sequence >= 1),
    type text not null,
    schema_version integer not null default 1 check (schema_version >= 1),
    visibility text not null default 'member'
        check (visibility in ('member', 'admin_redacted', 'internal')),
    payload jsonb not null,
    summary text not null default '',
    created_at timestamptz not null default now(),
    unique (task_id, sequence)
);

create index idx_events_task_sequence
    on events (task_id, sequence);

create table llm_calls (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    agent_run_id text references agent_runs(id) on delete set null,
    model_variant_id text references model_variants(id) on delete set null,
    request_tokens integer not null default 0 check (request_tokens >= 0),
    response_tokens integer not null default 0 check (response_tokens >= 0),
    cost numeric(18, 8) not null default 0 check (cost >= 0),
    latency_ms integer check (latency_ms is null or latency_ms >= 0),
    status text not null,
    fallback_from text references model_variants(id) on delete set null,
    error_category text,
    created_at timestamptz not null default now()
);

create index idx_llm_calls_task
    on llm_calls (task_id, created_at);

create table tool_calls (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    agent_run_id text references agent_runs(id) on delete set null,
    tool_binding_id text references workspace_tool_bindings(id) on delete set null,
    tool_key text not null,
    idempotency_key text,
    status text not null,
    latency_ms integer check (latency_ms is null or latency_ms >= 0),
    arguments jsonb not null default '{}'::jsonb,
    result_ref text,
    error_summary text not null default '',
    created_at timestamptz not null default now(),
    completed_at timestamptz
);

create unique index uq_tool_calls_idempotency
    on tool_calls (task_id, idempotency_key)
    where idempotency_key is not null;

create index idx_tool_calls_task
    on tool_calls (task_id, created_at);

create table evidence (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    source_type text not null,
    source_ref text not null,
    locator jsonb not null default '{}'::jsonb,
    title text not null,
    snippet text not null,
    checksum text,
    rank integer,
    created_at timestamptz not null default now()
);

create index idx_evidence_task
    on evidence (task_id, created_at);

create table results (
    id text primary key,
    task_id text not null unique references tasks(id) on delete cascade,
    answer text not null,
    completion_state text not null
        check (completion_state in ('complete', 'degraded', 'failed')),
    citations jsonb not null default '[]'::jsonb,
    limitations jsonb not null default '[]'::jsonb,
    confidence numeric(4, 3)
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    used_workflow text not null,
    created_at timestamptz not null default now()
);

-- Python-owned files, artifacts, and collaboration tables.
create table input_files (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    owner_user_id text not null references users(id),
    task_id text,
    object_key text not null,
    filename text not null,
    media_type text not null,
    size_bytes bigint not null check (size_bytes >= 0),
    sha256 text not null,
    parse_status text not null default 'pending'
        check (parse_status in ('pending', 'processing', 'completed', 'failed')),
    parse_error text not null default '',
    created_at timestamptz not null default now(),
    unique (workspace_id, object_key),
    foreign key (workspace_id, owner_user_id)
        references workspace_memberships(workspace_id, user_id),
    foreign key (task_id, workspace_id, owner_user_id)
        references tasks(id, workspace_id, owner_user_id)
        on delete set null (task_id)
);

create index idx_input_files_scope
    on input_files (workspace_id, owner_user_id, created_at desc);

create table document_blocks (
    id text primary key,
    input_file_id text not null references input_files(id) on delete cascade,
    block_index integer not null check (block_index >= 0),
    block_type text not null,
    locator jsonb not null default '{}'::jsonb,
    text text not null,
    metadata jsonb not null default '{}'::jsonb,
    search_vector tsvector generated always as (
        to_tsvector('simple', coalesce(text, ''))
    ) stored,
    unique (input_file_id, block_index)
);

create index idx_document_blocks_search
    on document_blocks using gin (search_vector);

create table artifacts (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    owner_user_id text not null references users(id),
    task_id text not null,
    object_key text not null,
    logical_name text not null,
    filename text not null,
    media_type text not null,
    size_bytes bigint not null check (size_bytes >= 0),
    sha256 text not null,
    version integer not null default 1 check (version >= 1),
    created_at timestamptz not null default now(),
    unique (workspace_id, object_key),
    unique (task_id, logical_name, version),
    foreign key (workspace_id, owner_user_id)
        references workspace_memberships(workspace_id, user_id),
    foreign key (task_id, workspace_id, owner_user_id)
        references tasks(id, workspace_id, owner_user_id)
        on delete cascade
);

create index idx_artifacts_scope
    on artifacts (workspace_id, owner_user_id, created_at desc);

create table resource_shares (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    resource_type text not null
        check (resource_type in ('task', 'input_file', 'artifact')),
    resource_id text not null,
    grantee_type text not null
        check (grantee_type in ('user', 'workspace')),
    grantee_id text not null,
    permission text not null check (permission in ('read', 'comment')),
    created_by text not null references users(id),
    created_at timestamptz not null default now(),
    unique (
        workspace_id, resource_type, resource_id,
        grantee_type, grantee_id, permission
    )
);

create index idx_resource_shares_grantee
    on resource_shares (workspace_id, grantee_type, grantee_id);

create table comments (
    id text primary key,
    task_id text not null references tasks(id) on delete cascade,
    author_user_id text not null references users(id),
    body text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

create table mentions (
    id text primary key,
    comment_id text not null references comments(id) on delete cascade,
    mentioned_user_id text not null references users(id) on delete cascade,
    read_at timestamptz,
    created_at timestamptz not null default now(),
    unique (comment_id, mentioned_user_id)
);

create table notifications (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    type text not null,
    resource_type text not null,
    resource_id text not null,
    payload jsonb not null default '{}'::jsonb,
    read_at timestamptz,
    created_at timestamptz not null default now()
);

create index idx_notifications_user_unread
    on notifications (user_id, created_at desc)
    where read_at is null;

-- Append-only; both services may append records, only Go exposes admin queries.
create table audit_logs (
    id text primary key,
    workspace_id text not null references workspaces(id) on delete cascade,
    actor_user_id text references users(id) on delete set null,
    source_service text not null check (source_service in ('gateway', 'runtime')),
    action text not null,
    resource_type text not null,
    resource_id text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index idx_audit_logs_workspace_created
    on audit_logs (workspace_id, created_at desc);