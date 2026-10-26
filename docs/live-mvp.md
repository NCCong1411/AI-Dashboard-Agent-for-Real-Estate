# Local live MVP

This workflow uses the existing Builder, OpenAI Responses adapter, Structured
Outputs, bounded draft edits, SQL compiler and ECharts renderer. It does not
deploy anything or require a new architecture.

## Enable live locally

Keep the existing local runtime and ALL/CA/TX reader-role database URLs. Add or
update these entries in the ignored `backend/.env`; never publish the key:

```dotenv
LLM_MODE=live
LLM_PROVIDER=openai
LLM_ENDPOINT=https://api.openai.com/v1
LLM_MODEL=gpt-6-luna
LLM_API_KEY=<your-private-key>
LLM_REASONING_EFFORT=low
LLM_MAX_CALLS_PER_RUN=1
LLM_INPUT_PRICE_PER_MILLION=0.125
LLM_OUTPUT_PRICE_PER_MILLION=0.50
LLM_PRICING_SOURCE=https://developers.openai.com/api/docs/models/gpt-6-luna
LLM_PRICING_VERIFIED_ON=2026-10-26
```

Recheck prices before copying this dated example. The official model page
lists Standard input/output at $0.10/$0.50 per million tokens and cache writes
at $0.125; the input setting above conservatively covers cache writes. This
profile uses explicit default service tier, no regional surcharge or Fast mode.
Account access, available credits and rate limits remain provider-dependent.
[Model capabilities and pricing](https://developers.openai.com/api/docs/models/gpt-6-luna),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Restart the local backend after changing settings, then sign in as a **local
Builder**, not the public Viewer. Existing local seed passwords are in the
ignored `backend/.runtime/local-demo-accounts.env`. Keep `LLM_MODE=mock` for
ordinary development/tests and do not copy these live settings to production.
Start the normal app with `scripts/start-local.ps1`. If your four local role
URLs are held in `backend/.runtime/local-postgres-credentials.env`, use
`scripts/start-local.ps1 -UseProvisionedRoles` instead; this existing mode
requires the corresponding four URL entries in `.env`/process environment to be
blank, avoiding competing credentials. Do not run the app as PostgreSQL admin.
See [local runbook](local-runbook.md) for role setup. The isolated acceptance
runner below needs none of these local DB credentials and leaves them intact.

The one-call profile fails visibly if the provider returns invalid/incomplete
output; it does not silently substitute mock. A user retry is a separate,
accounted run. Existing run/day budgets, 24,000-token reservation bound and
query/auth restrictions remain in effect. The reservation uses a conservative
UTF-8 byte bound including schema/instructions, not an exact tokenizer count.

## Catalog and editing

The model receives seven KPI definitions, deduplicated source metadata for
sales/offers/repairs, recent messages capped at 1,700 UTF-8 bytes and a compact
current spec (widget IDs and query meaning,
without layout or repeated versions). It never receives database rows/results,
credentials or an SQL execution tool. SQL is compiled from the validated plan.
Each query uses exactly one allowlisted fact; adding offers/repairs does not join
it into sales. Presentation edits preserve unrelated widgets and reuse results.
Unsupported sources/metrics must be explained, not replaced with invented values.
An available source with no matching rows is shown as `no_data`, not fabricated.

Try in one conversation:

1. `Tạo dashboard tổng giá trị giao dịch theo bang năm 2025`
2. `Thêm số lượng đề nghị mua bất động sản`
3. `Đổi biểu đồ doanh số thành cột ngang`
4. `Bỏ biểu đồ theo bang`
5. `Thêm số lượt khách xem tin bất động sản trên website` (no source; retain the last valid draft)

Only the requested state chart is created, so “doanh số” resolves to that chart.
With multiple matching sales charts, specify the dimension or expect a
clarification. Approving publishes the draft; without approval the existing
published version stays unchanged.

## Reproducible checks

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/unit -q
.\.venv\Scripts\python.exe backend/scripts/test_local_postgres.py --e2e

# Explicit paid opt-in; a new disposable loopback cluster, never production DB.
.\.venv\Scripts\python.exe backend/scripts/test_local_postgres.py --live-smoke
```

The live suite includes the exact catalog journey above and the existing
multi-edit/restart/approval/Viewer-scope journey. It sets the one-call limit only
for the paid app processes; deterministic budget regression retains its normal
configuration. Browser retries/repeats are disabled.
Both runners verify the 16 source-table fingerprints before/after and stop
their own cluster. Live token/cost accounting is exported from the real ledger
to `backend/.runtime/postgres-test-*/live-smoke-usage.json`; per-step evidence
is in `frontend/test-results-live/**/catalog-journey.json` and
`live-run-evidence.json`. These artifacts stay ignored and are not deployment
inputs. A live PASS requires actual API calls; mock/HTTP contract tests alone
do not prove live quality.

## Current verification (26 October 2026)

Backend unit tests: 271 PASS. PostgreSQL integration: 149 PASS and 3 restore
checks PASS. Mock browser acceptance: 19/19 PASS. Frontend lint, typecheck and
production build passed. The 16-table source fingerprints were unchanged.

After the operator replaced an invalid key, both actual `gpt-6-luna` browser
journeys passed (2/2):

- Exact catalog journey: 2025 sales by state, add purchase offer count as a
  separate fact query, horizontal sales chart, remove the state chart, then
  report the unavailable website-view source without changing the valid draft.
- Existing journey: five edits, unrelated-widget preservation, query reuse,
  backend restart recovery, approval idempotency, editing a published dashboard,
  independent Viewer filtering, and Viewer/foreign-Builder access denial.

The successful run recorded 12 settled calls, exactly one per turn, 38,803 input
tokens and 3,560 output tokens (including reported reasoning). Its conservative
ledger estimate is **$0.006636**, not an invoice. Evidence:
`backend/.runtime/postgres-test-6j6bewz1/live-smoke-usage.json`,
`backend/.runtime/postgres-test-6j6bewz1/source-integrity.json`, and the browser
artifacts described above. There is no remaining key/model blocker for this
verified local profile; availability/pricing may change.

An earlier valid-key attempt asked an unnecessary clarification for purchase
offers. The catalog description was corrected using the existing data dictionary
(`offers`: one purchase offer); formulas, schema, data and SQL were not changed.
The earlier invalid-key attempt retained a $0.005047 unknown-usage reservation,
and the clarification attempt settled $0.001156. These are separate from the
successful-run estimate; repeated paid test runs consume additional budget.
No production services or database were accessed/deployed by this work.

## Changed files

- Runtime: `backend/app/agent/{prompts,llm,nodes,mock}.py` and
  `backend/app/analytics/catalog.py`.
- Test runner/accounting evidence: `backend/scripts/test_local_postgres.py`.
- Backend regression: `backend/tests/unit/test_llm_adapter.py`, `test_edits.py`,
  `test_agent_filter_values.py`, and `test_local_postgres_runner.py`.
- Browser journey: `frontend/tests/support/catalog-journey.ts` plus
  `frontend/tests/{e2e,live}/catalog-journey.spec.ts`.
- Instructions: `README.md` and this document.
