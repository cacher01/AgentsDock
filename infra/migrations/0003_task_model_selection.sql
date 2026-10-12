-- Optional task-level model selection. Null preserves automatic model routing.
alter table tasks
    add column requested_model_variant_id text
    references model_variants(id) on delete set null;

create index idx_tasks_requested_model
    on tasks (requested_model_variant_id)
    where requested_model_variant_id is not null;