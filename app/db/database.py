import sqlite3


def init_database(path: str = "orchestration.db") -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    _create_tables(connection)
    return connection


def _create_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists sessions (
            session_id text primary key,
            title text not null,
            status text not null default 'active',
            created_at text not null,
            updated_at text not null
        );

        create table if not exists tasks (
            task_id text primary key,
            session_id text,
            input text not null,
            objective text not null default '',
            workflow text,
            status text not null,
            token_estimate integer not null default 0,
            error_summary text not null default '',
            created_at text not null,
            updated_at text not null,
            completed_at text
        );

        create table if not exists agents (
            agent_id text primary key,
            task_id text not null,
            name text not null,
            template text not null,
            goal text not null,
            context_brief text not null,
            allowed_tools text not null,
            status text not null,
            created_at text not null,
            updated_at text not null
        );

        create table if not exists events (
            event_id text primary key,
            task_id text not null,
            agent_id text,
            type text not null,
            timestamp text not null,
            sequence integer not null,
            payload text not null,
            summary text not null
        );

        create table if not exists messages (
            message_id text primary key,
            task_id text not null,
            sender_agent_id text,
            receiver_agent_id text,
            type text not null,
            payload text not null,
            created_at text not null
        );

        create table if not exists tool_calls (
            tool_call_id text primary key,
            task_id text not null,
            agent_id text,
            tool_name text not null,
            arguments text not null,
            status text not null,
            result_summary text not null default '',
            result_json text not null default '',
            error text not null default '',
            created_at text not null,
            completed_at text
        );

        create table if not exists evidence (
            evidence_id text primary key,
            task_id text not null,
            title text not null,
            url text not null,
            snippet text not null,
            source text not null,
            rank integer not null,
            source_type text not null,
            summary text not null,
            created_at text not null
        );

        create table if not exists results (
            result_id text primary key,
            task_id text not null unique,
            answer text not null,
            citations text not null,
            limitations text not null,
            confidence real not null,
            used_workflow text not null,
            created_at text not null
        );

        create table if not exists artifacts (
            artifact_id text primary key,
            task_id text not null,
            filename text not null,
            media_type text not null,
            size_bytes integer not null,
            relative_path text not null,
            created_at text not null
        );

        create index if not exists idx_artifacts_task_id
        on artifacts (task_id, created_at);
        """
    )
    _ensure_column(connection, "tasks", "session_id", "text")
    _ensure_column(connection, "tool_calls", "result_json", "text not null default ''")
    connection.commit()


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    columns = {row[1] for row in connection.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"alter table {table} add column {column} {declaration}")
