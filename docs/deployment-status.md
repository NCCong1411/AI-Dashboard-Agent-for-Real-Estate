# MVP deployment status

Verified on 24 October 2026 (Asia/Saigon).

- URL: https://aidashboardncc.duckdns.org
- Provider: AWS Lightsail, `us-east-1a`, instance `ai-dashboard-mvp`.
- Bundle: `micro_3_0`, $7/month base price, 1 GB RAM, 40 GB SSD, IPv4.
- OS: Ubuntu 24.04; Docker Compose; 2 GB swap.
- Current public IPv4: `44.222.76.41`. Recheck after stop/start and update DuckDNS.
- Release images: `ai-dashboard-mvp/backend:20261024` and
  `ai-dashboard-mvp/frontend:20261024-yellow1`, built locally and transferred over SSH.
- Compose files: `compose.prod.yaml` plus `compose.images.yaml`.
- Server release directory: `/opt/ai-dashboard`.
- Application: production mode, mock LLM, fresh synthetic database and accounts.

## Verification

The pale-yellow frontend was deployed on 24 October 2026: background
`#FFF9E6`, primary buttons `#F4D675`, text `#302A1E`. Frontend lint and production
build passed; production dependency audit found no vulnerabilities. Desktop
and mobile login screenshots were reviewed, including exact computed colors
and no horizontal overflow at 390 px. Main text/link/button contrast checks
all exceeded 4.5:1. Public CSS assets and real Viewer/Builder login, dashboard
queries, and logout were verified after rollout. Only the frontend container
was recreated; the backend start timestamp remained unchanged. A pre-update
DB backup was verified and copied to the private local file
`deploy/secrets/before-yellow-theme.dump`. The server's previous image selection
is saved at `deploy/secrets/compose.images.before-yellow.yaml` for rollback.

The source backup was inspected before migrations; Alembic reached
`0005_graph_checkpoints`. Runtime and reader role checks passed for ALL/CA/TX.
All application containers are healthy. Public HTTPS `/api/health` responds 200.
`deploy/scripts/smoke_https.py` passed real login/logout, secure cookie,
dashboard query, scope, and read-only Viewer authorization checks for ALL/CA/TX.
No write/delete dashboard tests were run on the live database.

After smoke checks, the host reported approximately 352 MB available memory
and 107 MB swap in use. This supports a small demo, not a concurrency/load
guarantee. The bundle must not be upgraded automatically.

## Private handoff

Viewer-only credentials are saved locally at
`backend/.runtime/lightsail-viewer.env` and on the server at
`deploy/secrets/viewer-demo.env`. These files are private and ignored by Git.
Builder credentials were also downloaded at the operator's request to the
private local file `backend/.runtime/lightsail-accounts.env`. Database secrets
remain on the server. The Builder password was rotated on 24 October 2026;
existing Builder sessions were revoked and HTTPS login/logout verified. The
server seed file and private local account file were synchronized afterward.

An initial PostgreSQL logical backup was inspected on the server and copied
to `deploy/secrets/lightsail-initial.dump` locally (ignored by Git). It is not
a continuous backup. Before an upgrade, make and verify a fresh backup and
retain it off the VPS. A PostgreSQL dump does not contain cluster-level reader
login passwords; retain the server's private role credential files separately
for disaster recovery. No paid automatic snapshot add-on has been enabled.

Credit eligibility for Lightsail was verified privately before provisioning.
Credit balances and expiration dates are not tracked in this public repository.
Credit exhaustion/expiration does not automatically delete this host.
Stopping Lightsail does not stop bundle billing.

The public demo account is documented in the README; Builder/database/AWS
credentials are excluded from Git. See `docs/vps-deploy.md` for the image-based
update procedure.

The current public Viewer username is `viewer`. Its README password was
explicitly approved for publication by the operator. Login, live charts,
Builder API denial (403), and logout were verified using that public account.
