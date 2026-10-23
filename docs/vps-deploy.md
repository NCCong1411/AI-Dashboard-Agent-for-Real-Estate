# MVP deployment: one Ubuntu VPS

This is a **new demo database**, not a copy of the developer's local users,
conversations, dashboards, or secrets. The first release is public Viewer access
and `LLM_MODE=mock`; only an operator knows the Builder passwords. No OpenAI key
is needed on the VPS. The only public containers are Caddy's ports 80/443.

## 1. External prerequisites

**Selected deployment: AWS Lightsail**, `us-east-1`, Ubuntu 24.04,
`micro_3_0` (1 GB RAM, 40 GB SSD, IPv4), $7/month base price before taxes,
snapshots, or traffic overage. This is a low-traffic mock demo. Credits cover
eligible usage only while the associated credit remains valid and available;
they do not make the resource permanently free. Stopping a Lightsail instance
does not stop its bundle charges. Do not enable paid add-ons without reviewing
their cost. A static IP is not provisioned by this release; after stop/start,
check the address and update DuckDNS if it changes.

The host bootstrap is `deploy/scripts/lightsail-bootstrap.sh` and adds 2 GB
of swap. Build application images locally, transfer them over SSH, and load
them with `docker load`; do not compile Next.js on the 1 GB host. Use both
`-f compose.prod.yaml -f compose.images.yaml` for **every** Compose command on
this host. Set `DEPLOY_TAG` to the transferred backend release tag (default
`20261024`) and `FRONTEND_DEPLOY_TAG` to the frontend tag (default
`20261024-yellow1`). These tags are independent so a UI update does not
replace the backend container.
Start services using `up -d --no-build`, rather than the `--build` commands
shown below for larger VPSs. For updates, build and test a new release locally,
transfer/load its images, back up the DB, run migration, then recreate services
with the new release tags. For a frontend-only update, after loading its image,
use `up -d --no-deps --no-build --wait frontend`. Keep the previous image and
Compose image selection for rollback. A failed memory check is not permission to upgrade
the paid bundle automatically.

`deploy/scripts/lightsail_remote.py` obtains short-lived SSH credentials from
AWS and validates host keys returned by AWS; credentials are never printed.
It supports `ssh`, `upload`, and `download`. The AWS CLI must be authenticated
locally. Public SSH ingress is restricted to the operator's current public IP;
update that one rule if the operator's network changes. Only 80/443 are public
to everyone, and only after the application has been initialized.

On the VPS, `python3 deploy/scripts/smoke_https.py` checks HTTPS, secure session
cookies, dashboard data, ALL/CA/TX scopes, Viewer access restrictions, and logout.
It uses real demo logins and read-only dashboard queries, not data mutation
tests. It saves a Viewer-only login file privately without printing passwords.

**Alternative free provider:** Create one Oracle Cloud **Always Free** Ubuntu Arm VM using
`VM.Standard.A1.Flex`, 2 OCPUs/12 GiB RAM, in the account's home region.
Keep the boot/block volumes within the 200 GiB Always Free allowance. OCI also
includes up to five volume backups, but these have a quota, not an unlimited
weekly-retention promise. Check that the shape, image, volume, public IP, and
region are marked Always Free in the console **before** confirming creation;
do not upgrade to Pay As You Go just to bypass an “out of host capacity” error.
Oracle may reclaim idle Always Free instances, so maintain an off-VM backup.
This Compose stack uses Arm-compatible official images, but its full deployment
must still be smoke-tested on the selected VM.
Oracle normally requires a phone number and payment card for account
verification, even when using Always Free. Choose the home region carefully;
Always Free compute must be placed there. Do not mistake 30-day trial credits
for resources that remain Always Free afterward.

**Paid fallback:** One DigitalOcean Ubuntu VPS at 2 vCPU/4 GiB in Singapore;
enable weekly Droplet backups separately. Both options use the same application
deployment commands below.

Configure SSH-key access and the cloud firewall/security list: allow 22 only
from your administration IP, 80/443 from the Internet, and deny everything
else. Install Docker Engine and the Compose plugin from Docker's Ubuntu
instructions. Do not put credentials in a Git remote, shell history, issue,
or chat.

Register an available `<name>.duckdns.org` subdomain in your own DuckDNS account
and point its IPv4 record at the VPS. Check its public DNS answer before starting
Caddy. If the VPS IP later changes, update DuckDNS. Caddy obtains and renews
HTTPS certificates when DNS and ports 80/443 work.

Clone this repository onto the VPS. All commands below run from its root. Keep
`deploy/secrets/` on the VPS only; it is ignored by Git and excluded from Docker
build contexts. Use a dedicated non-root operator in the `docker` group only if
you understand that Docker access is effectively root access.
Set `HOST_UID` and `HOST_GID` in each operator shell before running Compose so
the one-shot role provisioning container writes its credentials as the host
operator, not as root:

```sh
export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"
```

## 2. First boot: source data, migration, roles

```sh
umask 077
python3 deploy/scripts/prepare_production.py init <name>.duckdns.org
docker compose -f compose.prod.yaml up -d db
docker compose -f compose.prod.yaml ps db
```

PostgreSQL imports the repository's synthetic SQL **only when its data volume is
empty**. Never delete or recreate that volume to re-run the import on a live
deployment. Take a logical backup of this initial state before migration:

```sh
docker compose -f compose.prod.yaml exec -T db pg_dump -U postgres -Fc real_estate_db > deploy/secrets/source-before-migration.dump
docker compose -f compose.prod.yaml exec -T db pg_restore --list < deploy/secrets/source-before-migration.dump > /dev/null
docker compose -f compose.prod.yaml run --rm -e MIGRATION_BACKUP_VERIFIED=true migrate
docker compose -f compose.prod.yaml run --rm provision
```

The last command is **read-only** and prints the connected cluster identifier.
Check that the target is `real_estate_db`, then supply that identifier in the
next command (do not include angle brackets):

```sh
docker compose -f compose.prod.yaml run --rm provision python scripts/provision_production_roles.py --apply --confirm-cluster <identifier>
python3 deploy/scripts/prepare_production.py finish
docker compose -f compose.prod.yaml run --rm seed
docker compose -f compose.prod.yaml run --rm seed python scripts/seed_dashboard.py --owner builder_all --confirm-demo
docker compose -f compose.prod.yaml run --rm backend python scripts/verify_production_roles.py
```

`finish` creates fresh account passwords and runtime/reader URLs in owner-only
files. The four role logins are separate from the migration superuser and are
checked against the existing RLS/security-invoker rules. If a role creation
reports an uncertain commit, preserve `deploy/secrets/roles/` and run
`docker compose -f compose.prod.yaml run --rm provision python scripts/provision_production_roles.py --reconcile`
before doing anything else. Do not repeat `--apply` blindly.

The public account is `viewer_all`; retrieve **only**
`SEED_VIEWER_ALL_PASSWORD` from `deploy/secrets/accounts.env` and share it with
reviewers alongside the web URL. Never publish `builder_all`, `builder_tx`, the
other Viewer credential, database URLs, or any file in `deploy/secrets/`.
This is a shared demo login, so sessions are not personal accounts.

## 3. Open the site and smoke-test

```sh
docker compose -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml ps
```

Open `https://<name>.duckdns.org`. Check the valid HTTPS certificate,
`/api/health`, login/logout with `viewer_all`, the published dashboard and KPI
filters. Test that Viewer cannot enter Builder mutation flows. Privately test
the `ALL`, `CA`, and `TX` roles using the role verifier and distinct accounts.
`/api/health` only proves the process responds; the role verifier and a real
dashboard request prove that PostgreSQL works. Production login throttling
limits failed attempts per username; it is process-local and resets on restart,
so monitor abuse and upgrade it before allowing public Builder use.

## 4. Backups, updates, and recovery

VPS volume backups (within your provider's quota) are one recovery layer;
additionally make a logical
PostgreSQL backup **before every deployment or migration**. Keep a copy outside
the VPS. To verify a backup can be restored, restore it into a separately named
disposable database (or isolated test cluster) and run read-only row checks;
never test restore over `real_estate_db`.

```sh
umask 077
docker compose -f compose.prod.yaml exec -T db pg_dump -U postgres -Fc real_estate_db > deploy/secrets/pre-update.dump
docker compose -f compose.prod.yaml exec -T db pg_restore --list < deploy/secrets/pre-update.dump > /dev/null
git pull --ff-only
docker compose -f compose.prod.yaml build backend frontend migrate
docker compose -f compose.prod.yaml run --rm -e MIGRATION_BACKUP_VERIFIED=true migrate
docker compose -f compose.prod.yaml up -d --no-deps backend frontend caddy
docker compose -f compose.prod.yaml run --rm backend python scripts/verify_production_roles.py
```

Run the full automated suite against a **separate** test database before an
update, not against production. After updating, repeat HTTPS/login/dashboard
smoke checks. If code-only rollback is needed, deploy the earlier Git commit;
if a migration changed data, restore the verified backup to a separate cluster
first and assess compatibility. Do not assume Alembic downgrade is safe.

To restore after VPS loss, provision a new VPS, install Docker, restore a weekly
image or the offsite PostgreSQL backup plus the private secret files, point
DuckDNS to the new IP, and run the same smoke checks. PostgreSQL roles are
cluster-level state and are **not** included by `pg_dump -Fc`; keep a protected
copy of the generated role URLs/credentials and re-provision/verify roles as
appropriate for the recovered cluster.

## Boundaries

### Rotate the public Viewer login from Windows

Save the desired `DEMO_USERNAME` and `DEMO_PASSWORD` (12-1024 characters) in
`backend/.runtime/lightsail-viewer.env`. From the repository root run:

```powershell
.\.venv\Scripts\python.exe deploy/scripts/sync_lightsail_viewer.py --apply
```

Without `--apply`, this command makes no changes. It uploads the private file
over verified SSH, updates the original active ALL Viewer transactionally,
hashes the new password and revokes that user's sessions. For subsequent
rotations pass `--old-username` with the current username. It does not change
Builder credentials. If the connection is lost after submitting the update,
check login before retrying, because the database commit may have completed.

After renaming, do not re-run `seed_users.py`: it still seeds the fixed
`viewer_all` name and would recreate it. The original HTTPS smoke script also
uses the initial seeded account name; update its account selection before
using it again with a renamed Viewer.

### Rotate the private Builder password

From the repository root run the following and enter the password at the hidden
prompt (do not place it in the command line):

```powershell
.\.venv\Scripts\python.exe deploy/scripts/rotate_lightsail_builder.py --apply
```

This rotates only `builder_all`, revokes its existing sessions, updates its
private seed password on the server, verifies HTTPS login/logout, and refreshes
`backend/.runtime/lightsail-accounts.env` with owner-only permissions. Other
accounts are preserved. Without `--apply`, it makes no changes. If interrupted,
check login before retrying because the DB commit may already have completed.

- Caddy is the only published service. Do not map ports 5432, 8000, or 3000 to
  the host or set `POSTGRES_HOST_AUTH_METHOD=trust`.
- `MIGRATION_BACKUP_VERIFIED=true` is supplied only for a migration after backup
  inspection; it is not a persistent runtime setting.
- Demo accounts are newly generated. Existing local credentials that were ever
  pasted into chat or screenshots must not be reused; rotate them separately.
- Enabling a real LLM later is a separate change requiring a fresh key, current
  model pricing, strict budgets, abuse protection, and paid acceptance testing.
