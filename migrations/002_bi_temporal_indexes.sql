-- 001/FR-002 — indexes for bi-temporal + class + scope + TTL + time-scope queries.

-- Default read-path filter: valid_until IS NULL means "currently true"
CREATE INDEX IF NOT EXISTS documents_current_idx
  ON documents(valid_until, id) WHERE valid_until IS NULL;

-- InjectionSet computation: filter by class + scope
CREATE INDEX IF NOT EXISTS documents_class_scope_idx
  ON documents(class, scope);

-- TTL reaper sweep target
CREATE INDEX IF NOT EXISTS documents_expires_idx
  ON documents(expires_at) WHERE expires_at IS NOT NULL;

-- Time-scoped auto-pin lookup
CREATE INDEX IF NOT EXISTS documents_time_scope_idx
  ON documents(time_scope) WHERE time_scope IS NOT NULL;
