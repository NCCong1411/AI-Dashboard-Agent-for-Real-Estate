# Local runbook — database roles and analytics RLS

## Backup and restore rehearsal

Run from the repository root in PowerShell. `MIGRATION_DATABASE_URL` identifies
the source for `scripts/backup-db.ps1`; set it through your local secret workflow.
Never put a real URL in source control or a shared command transcript.

```powershell
.\scripts\backup-db.ps1 -OutputDirectory .\backups
```

Keep the generated `.dump` and `.dump.sha256` together. Provision a separate
PostgreSQL test cluster and an empty database with `test` in its name. Set
`RESTORE_TEST_DATABASE_URL` in the process environment to that target, then run:

```powershell
.\scripts\restore-db.ps1 -BackupPath .\backups\pre-migration-YYYYMMDD-HHMMSS.dump -ConfirmSeparateTestDatabase
```

Use the actual filename printed by backup. The confirmation attests that the
target cluster is separate; the script also rejects a known identical source
endpoint, a non-test name, a missing/mismatched SHA-256, and a non-empty target.
Hostname aliases are not proof of separate servers: verify cluster identity
when provisioning. Restore uses a single transaction, without `--clean`, owner
or privilege restoration. Repeating against the populated target is refused.
The connection environment is restored even on failure. No database is created,
dropped or cleaned by this command. The target operator must prevent concurrent
writes while restoring.

After restore, compare source counts/content and independent KPI SQL, then follow
the migration/role steps below. `MIGRATION_BACKUP_VERIFIED=true` is an operator
attestation after this rehearsal, not a replacement for it. Restoring an already
migrated backup also requires reprovisioning the cluster-wide login/group roles;
this archive intentionally excludes owner/ACL assignments.

For a fully isolated regression on the imported synthetic dataset:

```powershell
.\.venv\Scripts\python.exe backend/scripts/test_local_postgres.py
.\.venv\Scripts\python.exe backend/scripts/test_local_postgres.py --e2e
.\.venv\Scripts\python.exe backend/scripts/test_local_postgres.py --benchmark
```

The runner creates a disposable loopback cluster with random credentials, checks
restore and refusal of a second restore, runs all integration gates, fingerprints
the 16 source tables, and stops its cluster. Artifacts stay under ignored
`backend/.runtime/`; no command targets the existing user database. `--benchmark`
uses a production frontend, 3 excluded warmups and 30 measured samples per group.
Its AI group is mock and cannot establish live-provider performance or quality.
Run performance measurements without competing test/build workloads for a clean
baseline; report any overlap as a limitation.

Next.js 16 refuses a second `next dev` in the same frontend directory. If the
local app is already running on port 3000, the direct `--e2e` command above
will stop before Playwright; do not kill the user's app just to free its lock.
Run `backend/scripts/verify_clean_setup.py --e2e` from the repository root
instead: it copies Git-visible source (not real env/secrets) into a separate
snapshot, reinstalls from lockfiles and runs the PostgreSQL/browser gates there.
This costs more setup time but leaves the existing local app running.

```powershell
.\.venv\Scripts\python.exe backend/scripts/verify_clean_setup.py --e2e
```

## Dependency audit limitation (16 October 2026)

`npm audit --omit=dev` reports no production vulnerabilities. Full audit reports
five high entries from one lint-only dependency chain:
`eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces`.
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
affects braces through 3.0.3; the advisory and npm registry currently provide no
patched version. The issue is stack exhaustion on deeply nested brace patterns.
Do not pass user-supplied glob patterns to developer tooling. The app's request
and dashboard code does not invoke this lint chain; keep lint runs restricted
to trusted project patterns. Do not run `npm audit fix --force`: npm suggests a
breaking downgrade to eslint-config-next 14 that conflicts with the pinned
Next.js 16 stack. Recheck upstream for a compatible fix before final handoff.
This is an unresolved development-tool dependency advisory, not a claim of an
audit-clean full dependency tree.

## Safety boundary

Migration `0002_analytics_security` changes privileges and RLS metadata only. It does not insert, update, delete, truncate, or re-import source rows. Run it first on a separately restored test database whose name contains `test`; never use a runtime or reader credential for Alembic. The rehearsal database **must run in a separate PostgreSQL cluster/instance**, not merely another database in the production/local cluster: PostgreSQL roles are cluster-wide and the three fixed reader group names intentionally fail closed when already present.

Prerequisites for the migration account:

- the P1-T02 backup gate has been completed;
- it can alter the four source tables `public.properties`, `public.transactions`, `public.offers`, and `public.repairs`;
- it can create the three fixed `NOLOGIN` group roles below;
- no role named `analytics_reader_all`, `analytics_reader_ca`, or `analytics_reader_tx` already exists;
- the four source tables have no pre-existing RLS configuration. The migration fails closed instead of replacing one.
- no source table/column grants to `PUBLIC`, no `PUBLIC CREATE` on `public`, `analytics`, or `app`, and no unreviewed non-owner consumers of the four scoped tables. Privileges are additive and enabling RLS would default-deny existing consumers, so the migration aborts instead of silently weakening or breaking their access.

## Apply on the restored test database

```powershell
$env:MIGRATION_DATABASE_URL = '<migration-role-url-on-restored-test-db>'
$env:MIGRATION_BACKUP_VERIFIED = 'true'
Push-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
Pop-Location
```

The migration creates these passwordless group roles:

- `analytics_reader_all`
- `analytics_reader_ca`
- `analytics_reader_tx`

It never creates a login or stores a password. An operator creates three distinct login roles using the normal PostgreSQL secret-management workflow, grants each login exactly one group role, and ensures every login is `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`. Do not grant reader groups to one another. Do not grant readers database/schema `CREATE`, source ownership, or membership in an owner role.

Example membership commands, with environment-specific login identifiers substituted as SQL identifiers:

```sql
GRANT analytics_reader_all TO <all_login_role>;
GRANT analytics_reader_ca  TO <ca_login_role>;
GRANT analytics_reader_tx  TO <tx_login_role>;
```

Set passwords interactively or through the approved secret store; never add them to migration SQL, the repository, command history, or logs.

## Configure the backend

```powershell
$env:READER_ALL_DATABASE_URL = '<url-for-all-login>'
$env:READER_CA_DATABASE_URL = '<url-for-ca-login>'
$env:READER_TX_DATABASE_URL = '<url-for-tx-login>'
```

The backend requires an explicit `ALL`, `CA`, or `TX` scope and selects only the corresponding pool. Missing or unknown scope fails; there is no fallback to `ALL`. Reader usernames must be distinct from each other and from configured runtime/migration usernames. On the first connection in each pool, the backend verifies the actual PostgreSQL principal: exact expected group membership, no other inherited role, no superuser/BYPASSRLS/DDL/ownership path, and `INHERIT` enabled. A swapped DSN or over-privileged login fails before serving a query.

## Integration verification

Only point these variables at the separately restored test database:

```powershell
$env:READER_ALL_TEST_DATABASE_URL = '<all-reader-url-on-test-db>'
$env:READER_CA_TEST_DATABASE_URL = '<ca-reader-url-on-test-db>'
$env:READER_TX_TEST_DATABASE_URL = '<tx-reader-url-on-test-db>'
.\.venv\Scripts\python.exe -m pytest backend/tests/integration/test_rls.py -q
```

The test verifies direct tables and `security_invoker` views, CA/TX totals, offers/repairs isolation, PII denial, mutation/DDL denial, role attributes, ownership, and inability to switch into another scope. A skipped test is a blocker, not a pass.

## Provision local runtime and reader LOGIN roles

Use this only after the guarded main migration has completed on the already
migrated local `real_estate_db`. The command requires the ignored
`main-migration-preflight.json` and `main-migration-result.json` artifacts from
that migration. It also verifies the connected cluster identifier, Alembic head,
schemas, twelve RLS policies, four RLS-enabled tables, safe NOLOGIN reader groups,
and absence of all four reserved LOGIN names. It refuses non-loopback hosts.

The command reads `AUDIT_DATABASE_URL` directly from the ignored `backend/.env`.
It deliberately does not load `DATABASE_URL` or any runtime/reader setting. An
optional process-only `LOCAL_ROLE_ADMIN_DATABASE_URL` overrides it, but avoid
putting a URL containing a password directly in command history. The URL must be
a native `postgresql://` URI with one explicit loopback host, explicit user, and
the `real_estate_db` database. `hostaddr`, libpq services, non-loopback hosts,
SQLAlchemy driver URLs, and inherited `PGHOSTADDR`/`PGSERVICE` settings are
rejected before a connection is attempted.

First run the read-only preflight:

```powershell
.\.venv\Scripts\python.exe backend/scripts/provision_local_roles.py
```

The preflight prints the non-secret cluster system identifier. Review the target,
then repeat it explicitly in the opt-in apply command:

```powershell
.\.venv\Scripts\python.exe backend/scripts/provision_local_roles.py `
  --apply `
  --confirm-database real_estate_db `
  --confirm-cluster '<system-identifier-from-preflight>'
```

The transaction creates `dashboard_app_runtime` plus
`dashboard_reader_all`, `dashboard_reader_ca`, and `dashboard_reader_tx`.
Every login is non-superuser, cannot create databases or roles, cannot bypass
RLS, and owns no application object. Runtime receives only schema usage plus
metadata-table DML and sequence usage. Each reader receives exactly one existing
NOLOGIN scope group, a five-second statement timeout, and read-only transactions.
Role-name collisions abort; this command never rotates or overwrites an account.

Strong random passwords and the four ready-to-use URLs are written to
`backend/.runtime/local-postgres-credentials.env`, which is ignored by Git. No
password or URL is printed, and the command never edits `.env`. Restrict local
access to the workspace, move the values into the intended local secret store or
`backend/.env`, then securely remove the runtime artifact when it is no longer
needed. On Windows the command removes inherited ACL entries, grants only the
current user's SID full control, re-reads and verifies that ACL, and only then
writes secret bytes. If that verification fails, provisioning does not begin.
On non-Windows systems it similarly verifies an owner-only `0600` mode.

The roles, grants, memberships, policy ASTs and view security options are checked
again inside the provisioning transaction before `COMMIT`. If the connection is
lost during `COMMIT`, the process cannot know whether PostgreSQL committed. It
therefore preserves the credential artifact, reports state `uncertain`, and exits
with code 3. Do not delete the artifact or retry `--apply`; reconnect read-only
and reconcile the exact role state:

```powershell
.\.venv\Scripts\python.exe backend/scripts/provision_local_roles.py --reconcile
```

A successful reconciliation means the preserved URLs match a complete,
least-privilege role set. A failed reconciliation means the state is absent,
partial, or unsafe and requires manual inspection using the same local operator
account; the script never rotates, drops, or repairs roles automatically.

`scripts/start-local.ps1 -UseProvisionedRoles` can load the four URLs into the
backend child process directly from the protected artifact. It refuses to mix
them with effective populated runtime/reader URLs in `backend/.env` or the calling
process, and clears the temporary variables before starting the frontend. This
does not seed demo users. For a first-time local setup, run these from the
repository root with the virtualenv created in README:

```powershell
.\.venv\Scripts\python.exe backend/scripts/seed_local_demo.py
.\.venv\Scripts\python.exe backend/scripts/seed_local_demo.py --apply
.\.venv\Scripts\python.exe backend/scripts/seed_dashboard.py --owner builder_all --confirm-demo --use-provisioned-roles
.\.venv\Scripts\python.exe backend/scripts/verify_main_roles.py
```

The first command is a read-only preflight. The apply wrapper refuses existing
demo usernames, generates four random passwords, and writes them to the ignored
owner-only `backend/.runtime/local-demo-accounts.env` before calling the normal
seed service. The dashboard command explicitly publishes one sample dashboard.
The role verifier checks the actual DB login/scope boundary read-only. With
the app running, verify all four login/session flows and the scoped published
KPI through the frontend proxy:

```powershell
.\.venv\Scripts\python.exe backend/scripts/verify_local_app.py
```

Open `http://localhost:3000` and use the username/password pairs in the
private demo artifact. Do not send this file or its contents in chat, or pass
URLs/passwords as command-line arguments.

If a process-only override was used, clear it after provisioning:

```powershell
Remove-Item Env:LOCAL_ROLE_ADMIN_DATABASE_URL
```

Before downgrade, stop reader traffic. The downgrade removes the views, policies, grants, RLS enablement, and the three group roles; it does not modify source rows. Dropping a group also removes login memberships. The migration-cycle integration test re-provisions memberships from `READER_*_TEST_DATABASE_URL` after its final upgrade; an operator performing a manual cycle must do the same.

## Final live demo on the provisioned local database

After the local roles and demo accounts are provisioned, configure the live
provider/key/model/pricing in `backend/.env`. Stop the normal local backend
first: the MVP supports one backend process per database. Then run:

```powershell
.\.venv\Scripts\python.exe backend/scripts/verify_main_demo.py
```

This is a paid seven-turn journey. It uses existing demo credentials without
reseeding them, creates a dashboard with two approved versions in the main DB,
and leaves that dashboard available for demonstration. It copies current frontend
source to an ignored snapshot, installs locked dependencies, starts its own app
processes, and restarts only its backend to verify pending-draft recovery.
It checks five edits, repeated approval, Viewer scope/permissions, and visibility
before/after the second approval. Independent SQL verifies each executed plan;
read-only audit fingerprints verify all 16 source tables are unchanged.
Results, screenshots and logs stay under `backend/.runtime/main-live-demo-*`.
An unsuccessful run exits nonzero and retains evidence and any created draft.
Restart the normal app with `scripts/start-local.ps1 -UseProvisionedRoles`
after the command finishes. No migration or source-data write is performed.

## Auth seed details

Set `FRONTEND_ORIGIN` to the exact browser origin (default local value: `http://localhost:3000`). The backend rejects login/logout unless exactly one `Origin` header matches; validation errors never echo submitted values. Configure `RUNTIME_DATABASE_URL` with the non-owner application role, then supply all four `SEED_*_PASSWORD` values shown in `backend/.env.example` and run `backend/scripts/seed_users.py`. Passwords shorter than 12 characters are rejected and no default password exists. Re-seeding resets the four identities and revokes their active sessions in the same locked transaction.

For authentication integration tests, set `AUTH_TEST_DATABASE_URL` only to a database whose name contains `test`. The suite creates isolated random users, checks fixation/expiry/revocation and removes them afterward. Missing credentials cause a documented skip and remain a release blocker.
