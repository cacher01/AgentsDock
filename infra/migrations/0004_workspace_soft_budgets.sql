-- Workspace soft budgets guide model selection without hard-stopping synthesis.
create table workspace_budget_policies (
    workspace_id text not null primary key references workspaces(id) on delete cascade,
    task_token_soft_limit bigint not null default 100000
        check (task_token_soft_limit > 0),
    task_cost_soft_limit numeric(18,6) not null default 2.0
        check (task_cost_soft_limit > 0),
    daily_cost_soft_limit numeric(18,6) not null default 20.0
        check (daily_cost_soft_limit > 0),
    updated_by text not null references users(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);