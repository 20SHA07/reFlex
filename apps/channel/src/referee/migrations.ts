import type Database from "better-sqlite3";

export const SCHEMA_VERSION = 1;

export function migrate(db: Database.Database): void {
  db.pragma("foreign_keys = ON");
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY,
      root_path TEXT NOT NULL,
      workspace_key TEXT NOT NULL,
      channel_key TEXT NOT NULL,
      conversation_key TEXT NOT NULL,
      policy_version TEXT NOT NULL,
      member_ids_json TEXT NOT NULL,
      demo_operator_ids_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      kind TEXT NOT NULL CHECK(kind IN ('report','cleanup')),
      owner_id TEXT NOT NULL,
      owner_label TEXT NOT NULL,
      state TEXT NOT NULL,
      revision INTEGER NOT NULL,
      event_id TEXT NOT NULL,
      conversation_key TEXT NOT NULL,
      input_file_id TEXT,
      input_path TEXT,
      input_hash TEXT,
      draft_hash TEXT,
      narrative TEXT,
      metrics_json TEXT,
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tasks_project_state ON tasks(project_id, state);
    CREATE TABLE IF NOT EXISTS dependencies (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      task_id TEXT NOT NULL REFERENCES tasks(id),
      file_id TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      observed_hash TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      reason TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS dependencies_active ON dependencies(project_id, relative_path, active);
    CREATE TABLE IF NOT EXISTS report_revisions (
      task_id TEXT NOT NULL REFERENCES tasks(id),
      revision INTEGER NOT NULL,
      input_hash TEXT NOT NULL,
      draft_hash TEXT NOT NULL,
      staged_path TEXT NOT NULL,
      metrics_json TEXT NOT NULL,
      narrative TEXT NOT NULL,
      publication_operation_id TEXT,
      created_at TEXT NOT NULL,
      PRIMARY KEY(task_id, revision)
    );
    CREATE TABLE IF NOT EXISTS cleanup_actions (
      id TEXT PRIMARY KEY,
      cleanup_task_id TEXT NOT NULL REFERENCES tasks(id),
      project_id TEXT NOT NULL REFERENCES projects(id),
      file_id TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      proposed_reason TEXT NOT NULL,
      decision TEXT NOT NULL,
      state TEXT NOT NULL,
      revision INTEGER NOT NULL,
      expected_hash TEXT,
      blocking_task_ids_json TEXT NOT NULL,
      required_approver_id TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE(cleanup_task_id, file_id)
    );
    CREATE TABLE IF NOT EXISTS approvals (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK(kind IN ('cleanup','report_publish')),
      task_id TEXT NOT NULL REFERENCES tasks(id),
      action_ids_json TEXT NOT NULL,
      revision INTEGER NOT NULL,
      expected_hash TEXT,
      approver_id TEXT NOT NULL,
      workspace_key TEXT NOT NULL,
      channel_key TEXT NOT NULL,
      conversation_key TEXT NOT NULL,
      policy_version TEXT NOT NULL,
      process_session TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      consumed_at TEXT,
      cancelled_at TEXT,
      decision TEXT
    );
    CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id, cancelled_at, consumed_at);
    CREATE TABLE IF NOT EXISTS fs_operations (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK(kind IN ('quarantine','publication')),
      task_id TEXT NOT NULL REFERENCES tasks(id),
      action_id TEXT,
      source_path TEXT NOT NULL,
      destination_path TEXT NOT NULL,
      expected_hash TEXT NOT NULL,
      state TEXT NOT NULL,
      receipt_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_events (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      task_id TEXT,
      action_id TEXT,
      actor_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS inbound_events (
      provider_event_key TEXT PRIMARY KEY,
      processing_state TEXT NOT NULL,
      resulting_task_ids_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
  `);
  const current = db.prepare("SELECT version FROM schema_migrations WHERE version = ?").get(SCHEMA_VERSION);
  if (!current) db.prepare("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)").run(SCHEMA_VERSION, new Date().toISOString());
}
