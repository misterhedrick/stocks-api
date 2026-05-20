# Codex Handoff — stocks-api Migration Crisis

## What You Are Picking Up

This service is a FastAPI + SQLAlchemy + Alembic + PostgreSQL trading API hosted on Render.
It has been stuck in a deploy failure loop for many hours. The root cause is a stuck Alembic
migration (revision 0012) that cannot commit to the database due to PgBouncer zombie connections
blocking the `alembic_version` table update. Every deploy attempt exits with code 3 (uvicorn
lifespan startup failure) the moment the migration tries to run.

**The code fix (Plan B) is already committed and on master.** What remains is a manual DB
operation and one Render environment variable change to unblock the service.

---

## Current State of the Codebase

### Git branches
- `master` — Render deploy branch. Has Plan B code. Still failing to deploy because the DB is
  not yet unstuck.
- `develop` — Matches master.
- `claude/review-docs-AafuR` — Feature branch used during this session, merged.

### What Plan B changed (already merged to master)
1. `alembic/versions/0012_rename_paper_review_snapshots.py` — both `upgrade()` and `downgrade()`
   are now `pass` (true no-op). The migration's docstring and revision ID are unchanged.
2. `app/db/models.py` — `ReviewSnapshot.__tablename__` reverted to `"paper_review_snapshots"`.
   Constraint name reverted to `"uq_paper_review_snapshots_date_type"`.
3. `app/services/trading_reset.py` — display key reverted to `"paper_review_snapshots"`.
4. `app/services/retention_report.py` — display label reverted to `"paper_review_snapshots"`.
5. `app/services/review_snapshots.py` — TODO comment added at top of
   `create_or_update_post_market_review_snapshot()` explaining every step needed to complete
   the rename in a future maintenance window.
6. `tests/test_trading_reset.py` — assertions updated to match `"paper_review_snapshots"`.

### What did NOT change
- The model class is still `ReviewSnapshot` (Python name unchanged).
- All API routes, service file names, config fields, and schemas are unchanged.
- The actual DB table is still `paper_review_snapshots` (never successfully renamed).
- The `alembic_version` table in the DB is still at `0011_strategy_tuning_decisions`.

---

## Root Cause of the Deploy Failure

### The setup
- Render uses PgBouncer as a connection pooler in front of managed PostgreSQL.
- The app runs `alembic upgrade head` during startup (controlled by `AUTO_MIGRATE_ON_STARTUP`
  env var, currently `true`).
- Migration 0012 was originally a table rename (`paper_review_snapshots` → `review_snapshots`),
  which requires `ACCESS EXCLUSIVE` on the table.

### What went wrong
1. Previous migration attempts (before Plan B) tried to rename or create a VIEW over
   `paper_review_snapshots`. These required table locks.
2. The old running service instance held `ACCESS SHARE` locks on `paper_review_snapshots`
   (reading from the table during normal operation).
3. Each migration attempt queued an `ACCESS EXCLUSIVE` WAIT behind those readers.
4. PostgreSQL's `lock_timeout` (~7–8 seconds on Render's managed Postgres) fired, aborting the
   migration transaction.
5. PgBouncer kept the server-side connections alive after the client process exited — zombie
   connections with aborted-but-not-rolled-back transactions.
6. These zombie transactions now block the `UPDATE alembic_version` step that Alembic runs at
   the end of every migration, even a no-op `pass` migration.
7. Result: **every migration attempt for revision 0012 fails**, regardless of what the upgrade()
   function actually does.

### Evidence
- Every deploy exits at exactly ~7–8 seconds (lock_timeout) or ~2–3 seconds (shorter timeout
  hitting the alembic_version row lock from a zombie).
- One restart hung for 14 minutes (no lock_timeout on that PgBouncer connection) before Render
  killed it with "Port scan timeout reached."
- Even after replacing the migration body with `pass`, the deploy still fails at the same
  alembic_version commit step.

---

## What Needs to Happen (in order)

### Step 1 — Immediately unblock deploys (Render dashboard)

In the Render dashboard for the `stocks-api` service:
- Go to **Environment**
- Set `AUTO_MIGRATE_ON_STARTUP=false`
- Save and redeploy

With migrations disabled, Alembic never runs. The model already points to
`paper_review_snapshots` which exists in the DB. The service will start and serve traffic
normally. The `alembic_version` table staying at `0011` is harmless since 0012 is a no-op.

### Step 2 — Clear zombie connections and stamp the DB version (Render DB shell)

In the Render dashboard, open the database (`stocks_db_h2hj`) and use the **psql shell** or
**Connect** option to get a direct Postgres connection (not through PgBouncer if possible).

Run these SQL statements:

```sql
-- Kill zombie transactions left by failed migration attempts
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state IN ('idle in transaction', 'idle in transaction (aborted)');

-- Stamp alembic_version so revision 0012 is seen as already applied
UPDATE alembic_version
   SET version_num = '0012_rename_paper_review_snapshots'
 WHERE version_num = '0011_strategy_tuning_decisions';

-- Verify
SELECT version_num FROM alembic_version;
-- Expected: 0012_rename_paper_review_snapshots
```

If `pg_terminate_backend` is denied (Render's managed Postgres restricts it for non-superusers),
contact Render support and ask them to reset PgBouncer connections to the `stocks_db_h2hj`
database, or simply wait — PgBouncer's `idle_transaction_timeout` will eventually clean up
zombie connections on its own.

### Step 3 — Re-enable migrations (Render dashboard)

After Step 2 completes:
- Set `AUTO_MIGRATE_ON_STARTUP=true` in Render environment variables
- Redeploy

Alembic will now see `alembic_version = 0012_rename_paper_review_snapshots` (already at head)
and skip the migration entirely. The deploy will succeed normally.

### Step 4 — Re-enable cron jobs (Render dashboard)

The cron jobs were paused earlier to reduce DB lock contention during migration attempts.
Re-enable them once the service is confirmed healthy.

---

## Future Work — Completing the Rename (When Ready)

The actual DB rename (`paper_review_snapshots` → `review_snapshots`) was intentionally
skipped to unblock deploys. It should be completed in a maintenance window with no traffic.

The TODO comment in `app/services/review_snapshots.py` (at the top of
`create_or_update_post_market_review_snapshot`) lists the exact steps:

1. In a maintenance window with the service stopped (or cron jobs paused and no live traffic):
   ```sql
   ALTER TABLE paper_review_snapshots RENAME TO review_snapshots;
   ALTER INDEX uq_paper_review_snapshots_date_type RENAME TO uq_review_snapshots_date_type;
   ```

2. Update `app/db/models.py`:
   - `ReviewSnapshot.__tablename__ = "review_snapshots"`
   - `UniqueConstraint(..., name="uq_review_snapshots_date_type")`

3. Update `app/services/trading_reset.py`:
   - `(ReviewSnapshot, "review_snapshots")`

4. Update `app/services/retention_report.py`:
   - `"review_snapshots"` in the `always_preserved` list

5. Update `tests/test_trading_reset.py`:
   - Both `"paper_review_snapshots"` assertions → `"review_snapshots"`

6. Flip migration 0012 from no-op to the actual rename SQL (or create 0013 for the rename).

7. Update the TODO comment in `review_snapshots.py` (remove it once done).

---

## Key Constraints (Do Not Violate)

- `ALPACA_PAPER=true` and `AUTO_SUBMIT_REQUIRES_PAPER=true` must remain unchanged.
- Never commit directly to `develop` or `master`.
- All merges from `develop` → `master` require explicit human approval.
- The AI must never automatically apply strategy changes.
- All development goes on a feature branch first, then PR to develop, then develop to master.

---

## Service Architecture Quick Reference

- **Framework**: FastAPI + uvicorn
- **ORM**: SQLAlchemy 2.0.40 with Alembic 1.15.2
- **DB**: PostgreSQL on Render managed Postgres (via PgBouncer)
- **Deploy**: Render web service, auto-deploys on push to `master`
- **Health check**: `GET /health` → `{"status": "ok"}` (no DB check, used by Render)
- **Readiness check**: `GET /ready` (admin auth required, checks DB schema)
- **Migration trigger**: `AUTO_MIGRATE_ON_STARTUP` env var → runs `alembic upgrade head` in
  uvicorn lifespan. If migration raises → lifespan raises → uvicorn exit code 3.
- **Connection pool**: `NullPool` for migrations (fresh connection each run)
- **Key models**: `ReviewSnapshot` (`paper_review_snapshots`), `AiTradeReview`, `Signal`,
  `OrderIntent`, `BrokerOrder`, `Fill`, `Strategy`, `JobRun`, `AuditLog`
