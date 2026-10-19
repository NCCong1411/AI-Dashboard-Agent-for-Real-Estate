# Báo cáo nghiệm thu MVP local

> Trạng thái: **PASS theo tiêu chí nghiệp vụ đã duyệt — 19/10/2026**. Đủ 24 task; xem mục 38–39 cho demo cuối trên DB chính và đối chiếu bàn giao. Gate chất lượng dùng review ngữ nghĩa user đã duyệt: valid 19/20, other 10/10. Raw exact-match vẫn 21/30, `release_gate_passed=false`; không đổi oracle/grader. Các mục phía sau ghi cả lịch sử thất bại/chưa chạy tại từng thời điểm, không phải trạng thái hiện tại.

## 1. Thông tin lần chạy

| Trường | Giá trị |
|---|---|
| Report ID | `main-live-demo-ui3as7_6`; artifact SHA-256 ở mục 38 |
| Ngày Asia/Ho_Chi_Minh | 19/10/2026 |
| Commit/revision | Worktree chưa commit; frontend snapshot được giữ cùng artifact |
| Người chạy | Codex qua CLI/Playwright, review ngữ nghĩa do user duyệt phương pháp |
| Máy/OS/CPU/RAM | Windows build 26200, AMD64 Family 25 Model 68, 12 logical CPU, 16.329.842.688 byte RAM |
| Frontend version | App 0.1.0; Next.js 16.4.0, React 19.3.0, ECharts 6.1.0 |
| Backend version | App 0.1.0; Python 3.11.9, FastAPI 0.143.0, LangGraph 1.2.15 |
| PostgreSQL version/database | PostgreSQL 16.9 / `real_estate_db` |
| Dataset hash/row baseline | SHA-256 `c585831fc15942bfa3f3fa793d4d4ebedfde81905ae1214781431001043abbe3`; 16 bảng/2.203 dòng |
| Catalog version | `1.1.0-offers-repairs` |
| LLM mode/provider/model | Live / OpenAI / `gpt-6-luna`; app local thường vẫn theo `.env` mock |
| Pricing config | 0,125 USD/input và 0,50 USD/output mỗi triệu token; cấu hình đã xác minh 17/10/2026, nguồn lưu trong artifact benchmark; chi phí là ước tính |

## 2. Nguyên tắc kết luận

- Trạng thái cho từng gate: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`; không dùng `SKIP` như `PASS`.
- Mỗi `PASS` phải có command/test ID và artifact hoặc log không chứa secret/PII.
- Kết quả mock và provider thật báo riêng. Mock pass không chứng minh chất lượng LLM thật.
- Baseline file import và DB thật báo riêng. Chênh lệch phải được giải thích, không sửa dữ liệu để ép khớp.
- Plan đúng phải khớp KPI, dimension, filter, kỳ, chart, scope và authorization oracle. “SQL chạy được” chưa đủ.
- KPI tiền so bằng Decimal đến 0,01; tỷ lệ kiểm tra cả tử số và mẫu số; không chấp nhận fan-out.

## 3. Trạng thái milestone

| Milestone | Điều kiện | Trạng thái | Bằng chứng/blocker |
|---|---|---|---|
| M0 | Schema, bảy KPI, baseline và bộ 30 case rõ ràng | `PASS` | Mục 28–29: audit DB chính, golden 15/15 |
| M1 | Local app, login, RBAC và RLS | `PASS` | Mục 31, 38: runtime/reader login thật, bốn demo account |
| M2 | Dashboard từ DB không cần LLM | `PASS` | Mục 37–38: browser/SQL, scope ALL/CA/TX |
| M3 | Ngôn ngữ tự nhiên → draft | `PASS` theo review đã duyệt | Mục 36: valid 19/20, other 10/10; raw strict vẫn FAIL |
| M4 | Hội thoại, duyệt, phục hồi, đủ KPI | `PASS` | Mục 36–38: full KPI, năm edit, restart và hai approval |
| M5 | Test và bàn giao local | `PASS` | Mục 37–39: setup sạch, demo DB chính, runbook và audit hoàn tất |

### 3.1. P1-T01 — scaffold và cấu hình local

| Kiểm tra | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|
| Backend health trực tiếp | `GET 127.0.0.1:8000/api/health` → 200, payload `status=ok` | `PASS` | `backend/tests/test_scaffold.py`; startup smoke 12/10/2026 |
| Proxy cùng origin | `GET 127.0.0.1:3000/api/health` → cùng payload backend | `PASS` | chạy `scripts/start-local.ps1` với config từ hai file example |
| Thiếu config | Backend thiếu `APP_ENV` và frontend thiếu `BACKEND_URL` đều exit 1 với tên field/biến rõ ràng | `PASS` | config negative smoke |
| Không cần DB/LLM | App và health khởi động chỉ với `APP_ENV`; không có key/secret field trong P1 config | `PASS` | `test_scaffold_needs_no_database_or_llm_secret` |
| Dependency lock | Python `pip check` không lỗi; `npm ci --dry-run` exit 0; direct dependency đều pin exact | `PASS` | `backend/requirements.lock`, `frontend/package-lock.json` |
| Backend tests | 13/13 pass | `PASS` | `.venv/Scripts/python.exe -m pytest -q` |
| Frontend quality gates | lint, typecheck và production build đều exit 0 | `PASS` | `npm run lint`; `npm run typecheck`; `npm run build` |
| Ignore local artifacts | `.env`, `.env.local`, runtime log và database backup đều được ignore | `PASS` | `git check-ignore -v ...` |
| Startup/cleanup | Script mở đúng cổng 8000/3000; Ctrl+C dừng cả hai, hai cổng được giải phóng | `PASS` | local startup smoke; không để lại env tạm |

Kết luận P1-T01: **PASS**. Đây chỉ là scaffold/health; M1 vẫn `NOT_RUN` cho tới khi P1-T02–P1-T04 hoàn tất.

### 3.2. P1-T02 — schema metadata và migration an toàn

| Kiểm tra | Actual | Trạng thái | Bằng chứng/blocker |
|---|---|---|---|
| Metadata app | 9 bảng trong schema `app`; `analytics` được tạo rỗng cho task sau; không model nguồn `public` | `PASS` | `test_metadata_is_confined_to_app_schema` |
| Version/idempotency constraints | Unique dashboard/version, dashboard/draft hash, approval key, checkpoint identity và usage run; composite FK buộc `current_version_id` thuộc chính dashboard đó | `PASS` | `test_version_and_approval_idempotency_constraints_exist`; Alembic offline SQL |
| Credential separation | Runtime/migration dùng hai biến SecretStr; URL rỗng được coi là unset; từng operation chỉ đòi credential liên quan; cùng username và injected Alembic connection bị từ chối | `PASS` | `test_migration_requires_backup_and_separate_role`; `test_blank_urls_are_unset_and_operation_credentials_are_independent`; `test_injected_alembic_connection_is_rejected` |
| Backup gate | Alembic từ chối khi chưa có `MIGRATION_BACKUP_VERIFIED=true`; backup script hỗ trợ scheme psycopg, không đưa URL vào command line và fail-closed với query/fragment thay vì bỏ tùy chọn | `PASS` | `test_backup_script_rejects_url_options_instead_of_discarding_them`; PowerShell parser 0 lỗi |
| SQL migration/autogenerate offline | Tạo `app`/`analytics` và metadata; ownership filter loại reflected `public` table, không thể đề xuất DROP bảng nguồn | `PASS` | `test_autogenerate_filters_reflected_public_tables`; `test_offline_migration_sql_only_creates_owned_schemas` |
| Upgrade → upgrade → downgrade → upgrade | Chưa chạy trên database test đã restore | `BLOCKED` | thiếu `MIGRATION_TEST_DATABASE_URL` và `MIGRATION_TEST_BACKUP_RESTORED=true`; integration test skip |
| Row count nguồn trước/sau | Chưa đối chiếu bằng PostgreSQL integration | `BLOCKED` | cùng blocker DB test; test được định nghĩa trong `backend/tests/integration/test_migrations.py` |
| Runtime không có DDL/owner/superuser/BYPASSRLS | Test kiểm effective database/schema privilege và effective ownership của runtime bằng `pg_has_role(..., owner, 'USAGE')`, nên bắt cả owner role kế thừa có `SET FALSE`; sau đó dùng PostgreSQL 16 mode `SET` để audit đúng role thật sự switch được, kể cả ownership mà role đó kế thừa tiếp. Mỗi candidate còn bị negative DDL trong transaction, chỉ chấp nhận SQLSTATE `42501`. Chưa chạy bằng role runtime thật | `BLOCKED` | thiếu `RUNTIME_TEST_DATABASE_URL`; integration test skip |
| Backup restore vào DB riêng | Chưa có credential/target DB test để restore | `BLOCKED` | không chạy backup hay migration trên DB localhost chưa rõ quyền |

Kết quả suite tại lần review P1-T02: 23 pass, 2 skip có blocker; Alembic offline và PowerShell parser đều exit 0. Kết luận P1-T02: **BLOCKED**; không đánh dấu task hoàn tất cho tới khi toàn bộ integration/restore/privilege checks đạt trên DB test tách biệt.

### 3.3. P1-T03 — analytics views, reader roles và RLS

| Kiểm tra | Actual | Trạng thái | Bằng chứng/blocker |
|---|---|---|---|
| Reader routing | Ba URL/pool `ALL`/`CA`/`TX` tách biệt; scope thiếu/không hợp lệ bị từ chối, không fallback `ALL`; lúc mở connection kiểm principal thật, đúng duy nhất scope group, không role thừa, owner/DDL/BYPASSRLS hay quyền đọc cột ngoài allowlist | `PASS` (unit/static), live còn blocked | `test_reader_engine_has_no_default_scope_and_requires_distinct_roles`, `test_reader_factory_registers_live_principal_scope_validation` |
| Migration roles/RLS | Ba group role `NOLOGIN`, không password; 12 SELECT policies trên `properties`, `transactions`, `offers`, `repairs`; migration fail-closed nếu gặp RLS/role có trước, `PUBLIC` grant/CREATE không an toàn hoặc consumer scoped-table chưa review | `PASS` (static/offline) | `test_security_migration_has_rls_invoker_views_and_column_grants`, `test_security_migration_fails_closed_on_public_grants_and_existing_consumers` |
| Views và PII | `analytics.sales/offers/repairs` dùng `security_invoker`; column grants loại address/zip/client/description | `PASS` (static/offline) | `test_security_migration_contains_no_secret_or_source_dml` |
| Không đổi dữ liệu nguồn | Migration không có INSERT/UPDATE/DELETE/TRUNCATE; chỉ ALTER RLS/policy/grant/view/role metadata | `PASS` (static) | cùng static regression; Alembic offline SQL |
| Direct/view CA-TX và totals | Test thật đã định nghĩa cho totals sales/offers/repairs và kiểm không lộ state khác | `BLOCKED` | thiếu `READER_ALL_TEST_DATABASE_URL`, `READER_CA_TEST_DATABASE_URL`, `READER_TX_TEST_DATABASE_URL`; integration skip |
| Least privilege/cross-role | Test thật kiểm superuser/BYPASSRLS/CREATE/owner, effective column/mutation privilege, PII, mutation/DDL và `SET ROLE` chéo; denial phải có SQLSTATE `42501`, INSERT probe dùng FK hợp lệ | `BLOCKED` | cùng ba reader credentials trên DB test đã restore |

Kết quả full backend sau hardening/review P1-T03: 28 pass, 11 skip có blocker. Alembic offline exit 0, gồm 4 lệnh enable RLS, 12 policy và 3 view `security_invoker`; không lộ DSN secret. Runbook yêu cầu rehearsal trên cluster PostgreSQL tách biệt vì role là cluster-wide; migration-cycle test khôi phục reader membership sau upgrade cuối. Kết luận P1-T03: **BLOCKED**. Không đánh dấu task hoàn tất; static/offline evidence không thay thế kiểm tra bằng login role PostgreSQL thật.

### 3.4. P1-T04 — auth, session, RBAC và demo users

| Kiểm tra | Actual | Trạng thái | Bằng chứng/blocker |
|---|---|---|---|
| Password/session primitives | Argon2id; token ngẫu nhiên opaque, DB chỉ lưu SHA-256; expiry cấu hình | `PASS` (unit) | `test_argon2_password_and_opaque_session_token` |
| Cookie và CSRF/Origin | Cookie HttpOnly/SameSite=Lax, Secure ở production; login/logout yêu cầu đúng một header Origin khớp trusted origin; validation error bỏ `input`/`ctx` để không echo password/token | `PASS` (unit/static), live còn blocked | unit origin + sanitized HTTP validation tests; `test_auth.py` đã định nghĩa lifecycle thật |
| Identity/RBAC/ownership | `/me` đọc user/role/profile từ session DB; Viewer bị builder dependency từ chối; ownership query gắn server user ID và trả 404 khi không sở hữu | `PASS` (unit/static), DB còn blocked | `test_origin_and_builder_role_are_server_side_guards`; auth dependencies |
| Fixation/expiry/revocation | Login revoke token cookie cũ và cấp token mới; session hết hạn/logout bị từ chối | `BLOCKED` | thiếu `AUTH_TEST_DATABASE_URL`; PostgreSQL integration skip |
| Seed bốn account | Builder ALL, Viewer ALL, Viewer CA, Builder TX; password chỉ từ SecretStr environment; chạy lặp update cùng username, khóa row và revoke session cũ cùng transaction | `BLOCKED` | script/static test có; chưa chạy bằng runtime role trên DB test |
| Frontend login/user state | Trang `/login` gọi same-origin API, không nhận role/scope từ form; trang chính tải `/me`, hiển thị username/role/scope và có logout | `PASS` (build) | frontend lint, typecheck và production build exit 0 |

Kết quả full backend sau security review P1-T04: 32 pass, 13 skip có blocker. Frontend lint/typecheck/build đều exit 0 khi cấp `BACKEND_URL` hợp lệ. Login và seed cùng dùng row lock để credential reset không thể đua với session issuance. Kết luận P1-T04: **BLOCKED**; không đánh dấu task hoàn tất cho tới khi auth lifecycle, concurrency và seed idempotency chạy bằng runtime credential trên DB test.

### 3.5. P2-T01 — contracts và KPI catalog có version

| Kiểm tra | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|
| Pydantic contracts | `AnalysisPlan`, `DashboardSpec`, `WidgetSpec`, layout/query references, `QueryResult`, agent response đều strict và versioned | `PASS` | `test_analysis_plan_rejects_unknowns_and_requires_versions`; `test_dashboard_references_and_decimal_json_round_trip` |
| Catalog phase 2 | Đúng bốn KPI sales, cùng view `analytics.sales`; mapping dimension/filter cố định, grain/date/unit/default all-time rõ | `PASS` | `test_catalog_is_versioned_and_hides_sql_from_viewer`; catalog source |
| Semantic validation | Unknown KPI/dimension/filter/extra field, version sai, filter shape sai và sort field chưa chọn đều bị từ chối | `PASS` | `test_analysis_plan_rejects_unknowns_and_requires_versions` |
| Catalog RBAC | Builder thấy view/SQL/source allowlist; Viewer không nhận chi tiết SQL; identity đi qua `CurrentUser` server-side | `PASS` | catalog API TestClient với dependency identity cho hai role |
| Decimal JSON | Decimal đầu vào thành base-10 JSON string và round-trip giữ chính xác, không qua binary float | `PASS` | `test_dashboard_references_and_decimal_json_round_trip` |
| Generated TypeScript | Pydantic → OpenAPI → `openapi-typescript`; regeneration byte-for-byte khớp artifact và frontend typecheck | `PASS` | `test_generated_types_are_reproducible_from_openapi`; `npm run typecheck` |

Kết quả sau P2-T01: backend **36 pass, 13 skip** (skip vẫn là các integration credential blockers P1); frontend lint/typecheck/build đều exit 0, production dependencies audit 0 vulnerability. Kết luận riêng P2-T01: **PASS**. Các blocker live DB/auth của phase 0/1 vẫn mở và không được tính là đã đạt nhờ kết quả này.

### 3.6. P2-T02 — SQL compiler và AST validator

| Kiểm tra | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|
| Compiler từ contract | Chỉ nhận `AnalysisPlan`, gọi semantic validation, dùng mapping nội bộ cố định và sinh đúng một `SELECT` từ `analytics.sales AS s`; không nhận SQL/identifier từ request | `PASS` | `test_compiler_rejects_unvalidated_or_semantically_invalid_input`; `test_all_four_kpis_match_independent_golden_contract` |
| KPI/grain/dimension/date | Bốn biểu thức sales khớp golden: `SUM`, `COUNT(transaction_id)`, average có `NULLIF`, và `COUNT(DISTINCT property_id)`; đủ 8 dimension, group đúng biểu thức; ngày là khoảng nửa mở và all-time không tự thêm kỳ | `PASS` | `test_all_four_kpis_match_independent_golden_contract`; `test_every_dimension_has_a_catalog_owned_projection_and_grouping`; `test_all_time_plan_has_no_invented_date_bound` |
| Parameterization và filter types | Date, `eq`, từng phần tử `IN`, và limit đều tách khỏi SQL bằng named bind; payload có quote/comment/`OR 1=1` chỉ tồn tại trong parameter mapping. State/city/property type bắt buộc strict string; agent bắt buộc strict integer nên bool/string coercion bị từ chối | `PASS` | `test_dimensions_filters_sort_and_limit_use_only_fixed_mappings_and_binds`; filter type matrix trong `test_analysis_plan_rejects_unknowns_and_requires_versions` |
| AST source/function/structure allowlist | SQLGlot PostgreSQL parse fail-closed; chỉ một `SELECT`, đúng view/alias/cột/projection/predicate/group/order/limit. Chặn alias column remapping/source decoration, TABLESAMPLE, GROUP BY DISTINCT, ORDER WITH FILL, LIMIT WITH TIES, star, bảng/hàm/aggregate lạ, raw literal filter, `DISTINCT`/`INTO`/`OFFSET`, query không có KPI, join, subquery, set operation, mọi CTE, multi-statement và DML/DDL. Token/parse error được chuẩn hóa thành `SQLValidationError` | `PASS` | 31 negative fixtures trong `test_validator_rejects_sql_outside_allowlist`; malformed quote/comment, grouping/date negative tests |
| Golden/data baseline | Công thức compiler được đối chiếu trực tiếp với `golden_queries.sql`; fixture import độc lập chứng minh 1.000 transaction nhưng 125 property distinct và average dùng đúng tử/mẫu. Toàn bộ golden import evaluator vẫn pass | `PASS` (baseline import) | `test_all_four_kpis_match_independent_golden_contract`; `test_every_golden_result_matches_import_rows` |
| Dependency/lock | Pin `sqlglot==30.22.0`; chạy lại `pip-compile` cho lockfile cho cùng SHA-256, `pip check` không lỗi | `PASS` | `backend/pyproject.toml`; `backend/requirements.lock` |

Kết quả P2-T02 sau security review ngày 13/10/2026: test riêng compiler/validator **48 pass**; full backend **85 pass, 13 skip**. OpenAPI/TypeScript đã regenerate bằng pipeline một chiều; frontend lint/typecheck/build đều pass. Các skip chỉ là integration DB của P1 đã ghi blocker, không thuộc compiler/validator. Kết luận riêng P2-T02: **PASS**. Không có migration hay query nào được chạy trên DB chính; thực thi read-only, EXPLAIN/timeout/row limit và đối chiếu kết quả PostgreSQL live thuộc P2-T03. Baseline golden tại đây vẫn là file import dự thảo khi P0-T01 chưa có credential DB thật.

## 4. Gate dữ liệu và KPI

| Kiểm tra | Expected | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|---|
| DB thật chạy trong transaction read-only | `transaction_read_only=on` | Server nhận TCP; thiếu credential nên chưa mở transaction | `BLOCKED` | `pg_isready -h localhost -p 5432`; `python backend/scripts/audit_db.py --require-db` → exit 2 |
| 16 bảng / 16 PK / 16 FK | Khớp DB thật và import hoặc ghi rõ diff | Import: 16/16/16; DB: chưa xác minh | `BLOCKED` | `python backend/scripts/audit_db.py`; [data-audit.md](data-audit.md) |
| Duplicate/FK orphan | Báo số cụ thể | Import: PK duplicate 0, UNIQUE duplicate 0, FK orphan 0; DB: chưa xác minh | `BLOCKED` | `python backend/scripts/audit_db.py`; [data-audit.md](data-audit.md) |
| ALL transaction baseline | count 1.000; value 870.798.679,64 theo import | Import khớp; DB chưa xác minh | `BLOCKED` | [data-audit.md](data-audit.md) |
| CA transaction baseline | count 135; value 107.064.313,73 theo import | Import khớp; DB chưa xác minh | `BLOCKED` | [data-audit.md](data-audit.md) |
| TX transaction baseline | count 132; value 128.635.636,80 theo import | Import khớp; DB chưa xác minh | `BLOCKED` | [data-audit.md](data-audit.md) |
| `sold_property_count` | `COUNT(DISTINCT property_id)`, không phải transaction count | Import: ALL 125 distinct / 1.000 transactions; DB chưa chạy | `BLOCKED` | `backend/tests/test_golden_fixtures.py` pass; live DB blocked |
| `accepted_offer_rate` | accepted / tất cả offers cùng tập; mẫu số 0 → no data | Import: 35/125; empty = null/no_data; DB chưa chạy | `BLOCKED` | `backend/tests/test_golden_fixtures.py` pass; live DB blocked |
| `repair_cost` | SUM một fact theo `repair_date` | Import: 2.993.224,70; empty = null/no_data; DB chưa chạy | `BLOCKED` | `backend/tests/test_golden_fixtures.py` pass; live DB blocked |
| Không fan-out | Không query nào SUM sau join nhiều fact/many-to-many | 6 prepared queries đều chỉ dùng một fact; DB chưa chạy | `BLOCKED` | static fixture test pass; live DB blocked |
| Ngày biên/kỳ rỗng | Inclusive start, exclusive end; empty contract đúng | 6 ngày biên + ALL/CA/TX kỳ rỗng pass trên import | `BLOCKED` | `backend/tests/test_golden_fixtures.py` pass; live DB blocked |

Nguồn chuẩn ban đầu: `docs/kpi-catalog.md`, `backend/tests/fixtures/golden_queries.sql`, `backend/tests/fixtures/golden_results.json`. Hiện baseline DB thật còn `BLOCKED` nếu chưa có credential chỉ đọc.

P0-T02 provisional validation: `python -m pytest -q` trong `backend/` đạt 5/5 test trên file import. Task vẫn `BLOCKED` ở mức baseline cuối vì chưa thể chạy chính các prepared PostgreSQL query trên DB thật và đối chiếu sai lệch DB–import.

## 5. Đánh giá Agent — 30 yêu cầu tiếng Việt

Nguồn: `backend/tests/evaluation/prompts.json`.
Các bảng `NOT_RUN` ở mục 5–11 dưới đây là **template P0 ngày 12/10**, giữ để truy vết kế hoạch ban đầu; không phản ánh trạng thái hiện tại. Kết quả live gần nhất và adjudication theo lựa chọn user ở mục 36 và `docs/live-eval-review.md`.

### 5.1. Coverage bắt buộc

| Nhóm | Số case | IDs | Kết quả |
|---|---:|---|---|
| Hợp lệ | 20 | `valid_001`–`valid_020` | `NOT_RUN` |
| Mơ hồ | 5 | `ambiguous_001`–`ambiguous_005` | `NOT_RUN` |
| Không hỗ trợ | 5 | `unsupported_001`–`unsupported_005` | `NOT_RUN` |
| Phase 2 | 23 | `valid_001`–`valid_014`; `ambiguous_001`–`ambiguous_003`, `ambiguous_005`; `unsupported_001`–`unsupported_005` | `NOT_RUN` |
| Phase 4, phải đánh giá lại | 7 | `valid_015`–`valid_020`; `ambiguous_004` | `NOT_RUN` |

Validation fixture P0-T03: **PASS** — `python -m pytest -q` trong `backend/` kiểm tra schema/coverage/semantic consistency và toàn bộ suite đạt 10/10 test ngày 12/10/2026. Đây chỉ là nghiệm thu corpus/oracle; chưa case Agent nào được chạy nên các dòng kết quả bên trên vẫn `NOT_RUN`.

Phase 3 phải báo riêng pre-check phase 2 gồm 14 valid, 4 ambiguous và 5 unsupported; không tính 7 case phase 4 chưa triển khai là pass/fail LLM. Vì phase 2 chỉ có 14 valid, pre-check này **không thể** chứng minh ngưỡng 18/20 và không phải release gate. Tại Phase 4 chạy lại đủ 20 valid, 5 ambiguous, 5 unsupported; ngưỡng valid là ít nhất 18/20.

### 5.2. Cách chấm một case

Một case hợp lệ chỉ pass khi đồng thời đúng:

1. `outcome=plan`, toàn bộ KPI ID thuộc catalog/version mong đợi.
2. Dimension hợp lệ cho từng fact; ngày dùng đúng date field.
3. Filter, nửa khoảng ngày, sort, limit và chart khớp oracle.
4. Scope bằng giao của request với profile server; không mở rộng quyền.
5. Mixed-fact dashboard tạo query riêng, không join fact.
6. SQL thực thi cho kết quả khớp golden Decimal/tỷ lệ/grain.

Case mơ hồ chỉ pass khi trả `clarification`, hỏi đủ nội dung oracle và không query. Case không hỗ trợ chỉ pass khi từ chối đúng reason, không query và không bịa KPI. Authorization denial phải xảy ra trước Agent/DB.

Mỗi case phải lưu `case_id`, mode, provider/model/config, actual outcome, plan diff, SQL/result nếu áp dụng, scope/auth, latency, trạng thái và artifact. Chỉ `PASS` khi tất cả điều kiện của case cùng đạt; `BLOCKED`, `NOT_RUN` hoặc `SKIPPED` không được cộng vào số pass. Báo cáo mock và live tách riêng; mock không thay bằng chứng live.

### 5.3. Kết quả tổng hợp

| Chế độ | Provider/model/config | Valid đúng / tổng | Ambiguous đúng / 5 | Unsupported đúng / 5 | SQL/result đúng | Tokens | Chi phí ước tính | Trạng thái |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Mock | `<config>` | `<n>/<d>` | `<n>/5` | `<n>/5` | `<n>/<d>` | `<n>` | N/A | `NOT_RUN` |
| Live | `<provider/model>` | `<n>/<d>` | `<n>/5` | `<n>/5` | `<n>/<d>` | `<n>` | `<amount>` | `NOT_RUN` |

### 5.4. Chi tiết từng case

| Case ID | Phase | Expected outcome | Actual outcome | Plan diff | SQL/result | Scope/auth | Latency | Trạng thái | Artifact |
|---|---|---|---|---|---|---|---:|---|---|
| `<id>` | `<2/4>` | `<oracle>` | `<actual>` | `<diff>` | `<result>` | `<result>` | `<ms>` | `NOT_RUN` | `<path>` |

## 6. Ma trận quyền và bảo mật

| Kịch bản | Expected | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|---|
| Builder ALL tạo/chỉnh/duyệt dashboard mình sở hữu | Cho phép | `<actual>` | `NOT_RUN` | `<test>` |
| Viewer gọi create/message/approval | 403 trước Agent/query | `<actual>` | `NOT_RUN` | `<test>` |
| Builder khác owner đọc draft/checkpoint/approve | 403/404, không lộ tồn tại nhạy cảm | `<actual>` | `NOT_RUN` | `<test>` |
| Viewer CA mở dashboard đã duyệt | Chỉ dữ liệu CA | `<actual>` | `NOT_RUN` | `<test>` |
| Viewer CA yêu cầu TX | Empty/no data, không tự mở rộng ALL | `<actual>` | `NOT_RUN` | `<test>` |
| Reader CA/TX SELECT trực tiếp và qua view | Chỉ đúng bang | `<actual>` | `NOT_RUN` | `<RLS test>` |
| Reader INSERT/UPDATE/DELETE/DDL | Bị từ chối | `<actual>` | `NOT_RUN` | `<RLS test>` |
| Reader đọc PII | Bị từ chối | `<actual>` | `NOT_RUN` | `<grant test>` |
| Prompt yêu cầu đổi scope/bỏ validation/arbitrary SQL | Bị từ chối; không query | `<actual>` | `NOT_RUN` | `<adversarial test>` |
| CSRF/Origin không hợp lệ | Mutation bị chặn | `<actual>` | `NOT_RUN` | `<auth test>` |

## 7. HITL, phiên bản và phục hồi

| Kịch bản | Expected | Actual | Trạng thái | Bằng chứng |
|---|---|---|---|---|
| Preview thành công | Chỉ tạo draft `awaiting_approval`; Viewer chưa thấy | `<actual>` | `NOT_RUN` | `<test>` |
| Reject | Kết thúc lượt, không publish | `<actual>` | `NOT_RUN` | `<test>` |
| Approve đúng revision/hash/base version | Tạo đúng một version chính thức | `<actual>` | `NOT_RUN` | `<test>` |
| Stale draft/base version | HTTP 409 | `<actual>` | `NOT_RUN` | `<test>` |
| Double click/retry approval | Idempotent, một version | `<actual>` | `NOT_RUN` | `<test>` |
| Mất response sau commit rồi retry | Trả cùng kết quả, không duplicate | `<actual>` | `NOT_RUN` | `<test>` |
| Restart khi `awaiting_approval` | Khôi phục đúng conversation/draft | `<actual>` | `NOT_RUN` | `<test>` |
| Edit dashboard đã duyệt | Viewer thấy bản cũ đến lần approve mới | `<actual>` | `NOT_RUN` | `<test>` |
| Năm lượt edit liên tiếp | Giữ ID/widget không liên quan; filter tính lại | `<actual>` | `NOT_RUN` | `<test>` |

## 8. Lỗi, giới hạn và khả năng phục hồi

| Kịch bản | Expected | Actual | Trạng thái |
|---|---|---|---|
| LLM 429/timeout | Lỗi có cấu trúc; không ghi đè bản duyệt | `<actual>` | `NOT_RUN` |
| Structured output sai | Tối đa một vòng sửa; tính vào budget | `<actual>` | `NOT_RUN` |
| KPI/dimension không hỗ trợ | Clarification/refusal, không query | `<actual>` | `NOT_RUN` |
| DB timeout 5 giây | Lỗi có cấu trúc | `<actual>` | `NOT_RUN` |
| Query >1.000 dòng | Yêu cầu giảm chi tiết, không âm thầm truncate | `<actual>` | `NOT_RUN` |
| Một widget lỗi | Widget báo lỗi, trang còn hoạt động | `<actual>` | `NOT_RUN` |
| Session hết hạn | Bị chặn, không lộ draft/data | `<actual>` | `NOT_RUN` |
| Kỳ/scope rỗng | Trạng thái no data, không mở rộng scope | `<actual>` | `NOT_RUN` |

## 9. UI và end-to-end

| Kiểm tra | Expected | Actual | Trạng thái | Artifact |
|---|---|---|---|---|
| Dashboard card/line/bar/horizontal/pie/donut/table | Render đúng dữ liệu/schema | `<actual>` | `NOT_RUN` | `<screenshot/test>` |
| Loading/empty/error từng widget | Không phá toàn trang | `<actual>` | `NOT_RUN` | `<artifact>` |
| Resize/viewport | Chart resize đúng | `<actual>` | `NOT_RUN` | `<artifact>` |
| Nhãn dài/HTML | Hiển thị text, không eval | `<actual>` | `NOT_RUN` | `<artifact>` |
| Filter ngày/bang/loại | Số liệu tính lại đúng scope | `<actual>` | `NOT_RUN` | `<artifact>` |
| Viewer UI | Không có nút Agent/approve | `<actual>` | `NOT_RUN` | `<artifact>` |

## 10. Ngân sách và hiệu năng

| Metric/gate | Ngưỡng | Số mẫu | Actual | Error rate | Trạng thái |
|---|---:|---:|---:|---:|---|
| LLM calls/lượt | ≤3, gồm retry | `<n>` | `<actual>` | `<rate>` | `NOT_RUN` |
| Token/lượt | ≤24.000 | `<n>` | `<actual>` | `<rate>` | `NOT_RUN` |
| LLM time/lượt | ≤60 giây | `<n>` | `<actual>` | `<rate>` | `NOT_RUN` |
| Chi phí/lượt | ≤0,10 USD | `<n>` | `<actual>` | `<rate>` | `NOT_RUN` |
| Chi phí/ngày/Builder | ≤1 USD | `<n>` | `<actual>` | `<rate>` | `NOT_RUN` |
| Dashboard đã lưu P95 | <3 giây | ≥30 | `<actual>` | `<rate>` | `NOT_RUN` |
| Tạo AI P95 | <45 giây | ≥30 | `<actual>` | `<rate>` | `NOT_RUN` |

Ghi riêng warm-up, DB/LLM/render latency, model/config/dataset và concurrency. EXPLAIN cost không được báo như thời gian hoặc tiền.

## 11. Checklist demo nghiệm thu cuối

| Bước | Kịch bản | Expected | Actual | Trạng thái | Artifact |
|---:|---|---|---|---|---|
| 1 | Builder ALL tạo “giá trị giao dịch năm 2025 theo tháng, bang và loại bất động sản” | Khớp `valid_001` | `<actual>` | `NOT_RUN` | `<artifact>` |
| 2 | Kiểm tra ba KPI card, ba chart, nguồn KPI/SQL/kỳ | Đúng catalog/golden | `<actual>` | `NOT_RUN` | `<artifact>` |
| 3 | Năm edit: CA; cột ngang; thêm sold properties; bỏ widget; đổi kỳ | Diff đúng, widget khác giữ nguyên | `<actual>` | `NOT_RUN` | `<artifact>` |
| 4 | Restart backend lúc chờ duyệt | Khôi phục đúng draft | `<actual>` | `NOT_RUN` | `<artifact>` |
| 5 | Approve rồi retry cùng approval | Chỉ một version | `<actual>` | `NOT_RUN` | `<artifact>` |
| 6 | Viewer CA mở/filter, thử TX | CA đúng; TX no data | `<actual>` | `NOT_RUN` | `<artifact>` |
| 7 | Viewer mutation; tài khoản khác lấy draft | Đều bị chặn | `<actual>` | `NOT_RUN` | `<artifact>` |
| 8 | Builder sửa bản đã lưu | Viewer thấy bản cũ tới approve mới | `<actual>` | `NOT_RUN` | `<artifact>` |

## 12. Blocker, sai lệch và giới hạn

| ID | Mức độ | Mô tả | Ảnh hưởng gate | Owner | Hành động/điều kiện gỡ | Trạng thái |
|---|---|---|---|---|---|---|
| `P0-T01-DB-CREDENTIAL` | Cao | PostgreSQL nhận kết nối tại localhost:5432 nhưng không có credential/DSN chỉ đọc | Xác minh DB thật, diff DB-import và hoàn tất P0-T01 | Chủ môi trường DB | Cấp `AUDIT_DATABASE_URL` cho role chỉ đọc rồi chạy `python backend/scripts/audit_db.py --require-db` | `open` |
| `P1-T02-MIGRATION-TEST` | Cao | Chưa có DB test đã restore, migration credential và runtime credential tách biệt | Upgrade/downgrade, bảo toàn nguồn, restore và least privilege | Chủ môi trường DB | Cấp ba biến test theo README, restore backup vào DB có tên chứa `test`, chạy integration suite | resolved on isolated test DB; see section 14 |
| `P1-T03-READER-CREDENTIALS` | Cao | Chưa có ba login reader ALL/CA/TX trên DB test đã restore | Chưa xác minh RLS, security-invoker view, PII/DDL denial và chống đổi scope bằng PostgreSQL thật | Chủ môi trường DB | Cấp ba `READER_*_TEST_DATABASE_URL` theo `docs/local-runbook.md`, mỗi login chỉ thuộc đúng một reader group, rồi chạy `test_rls.py` | resolved on isolated test DB; see section 14 |
| `P1-T04-AUTH-TEST` | Cao | Chưa có runtime credential trên DB test đã migrate | Chưa chạy lifecycle login/fixation/expiry/logout và seed lặp bằng PostgreSQL thật | Chủ môi trường DB | Cấp `AUTH_TEST_DATABASE_URL`, chạy `test_auth.py`, sau đó chạy seed hai lần với bốn password environment và xác minh chỉ bốn username | resolved on isolated test DB; see section 14 |

Blocker đã biết tại thời điểm tạo template: chưa có credential read-only để xác minh DB thật; chưa được phép coi baseline import là baseline DB.

## 13. Kết luận và ký xác nhận

| Vai trò | Tên | Kết luận | Ngày | Ghi chú |
|---|---|---|---|---|
| Kỹ thuật | `<name>` | `<PASS/FAIL/BLOCKED>` | `<date>` | `<notes>` |
| Nghiệp vụ | `<name>` | `<accepted/rejected>` | `<date>` | `<notes>` |

Kết luận tổng: `<NOT_RUN/PASS/FAIL/BLOCKED>`. Chỉ đánh dấu MVP hoàn tất khi mọi gate bắt buộc có bằng chứng và không còn blocker bắt buộc.

## 14. Cập nhật triển khai 15/10/2026 — executor và dashboard không cần LLM

### PostgreSQL test thực, tách DB chính

Command: `.venv/Scripts/python.exe backend/scripts/test_local_postgres.py --e2e`.

Script dùng PostgreSQL **16.9** đã cài, tạo cluster loopback/cổng trống riêng trong thư mục ignored `backend/.runtime/`. Import dataset synthetic vào `dashboard_source`, `pg_dump -Fc`, kiểm `pg_restore --list`, restore `dashboard_test`; tạo login migration/runtime và ba reader riêng với credential ngẫu nhiên, không in secret. Cluster và app processes dừng trong `finally`. Artifact lần chạy đã pass: `backend/.runtime/postgres-test-4ydo5hac/`.

| Gate | Kết quả và bằng chứng |
|---|---|
| P1-T02 migration/restore | **PASS trên DB test**: 2 integration test; upgrade/downgrade/upgrade giữ count của đủ 16 bảng; runtime không có quyền DDL/ownership/effective privileged role. Sửa lỗi ACL cột rỗng làm `aclexplode` thất bại khi chạy migration thật. |
| P1-T03 RLS | **PASS trên DB test**: 9 test reader login ALL/CA/TX; trực tiếp/qua invoker view đúng scope và baseline; đọc PII, DML/DDL và switch role bị từ chối với `42501`; reader pool kiểm principal thật trước truy vấn. |
| P1-T04 auth | **PASS các integration hiện có trên DB test**: login/fixation/expiry/revocation/server identity và seed lặp đều chạy thực (2 test). UI đăng nhập hoạt động trong Playwright. Chưa xác minh DB chính hoặc benchmark concurrency. |
| P2-T03 executor | **PASS**: 23 integration test; 11 golden sales case gồm ngày biên, đủ 8 dimension không fan-out, scope luân phiên/giao rỗng, cost/row gate, transaction read-only/timeout/reset và timeout thật qua table lock rồi pool recovery. 29 unit cases bao gồm overflow 1001 dòng và lỗi DB không echo SQL/secret. |
| P2-T04 dashboard API | **PASS**: 2 integration test dùng auth cookie thực; cùng dashboard trả ALL=1000, CA=135; money đúng Decimal string, draft không có trong list/detail/query, SQL chỉ Builder, schema/catalog không tương thích trả 409, payload SQL/profile/user ID bị 422, Origin sai 403, mutation không có route. Filter giao với saved scope/date; seed idempotent bằng CLI, có audit, không có publish endpoint. |
| P2-T05 UI | **PASS**: 3 Playwright test trên frontend + backend + DB test thực; xem các giới hạn coverage bên dưới. |

Tổng integration: **38 pass, không skip** (2 migration + 36 auth/RLS/executor/dashboard). Unit/static/regression riêng: **122 pass**. Không sử dụng kết quả skip do thiếu environment trong lần chạy pytest thông thường làm bằng chứng này.

EXPLAIN: 27 plan hợp lệ (tổng + tám dimension × ba scope), root Total Cost max **3905.65**, ghi tại `explain-costs.json` trong artifact. Chọn mặc định `ANALYTICS_MAX_QUERY_COST=10000`; đây là planner units với headroom cho dataset mẫu, cần hiệu chỉnh lại theo statistics/dữ liệu DB chính. Không suy latency hoặc tiền từ cost. Hard bounds: `statement_timeout <= 5000ms`, `max_rows <= 1000`; SQL được compile/validate nội bộ và thực thi trong transaction read-only.

### Frontend, contracts và browser coverage

- `npm run generate:api`: Pydantic/OpenAPI → TypeScript; regeneration test byte-for-byte đạt.
- `npm run typecheck`, `npm run lint`, `npm run build`: đạt. Build có `/dashboards` và `/dashboards/[id]`.
- `npm audit --omit=dev --json`: 0 vulnerability cho production dependencies trong lần kiểm tra.
- Playwright dùng dữ liệu DB thực để kiểm ALL/CA totals, lọc năm 2025 (CA=15, sales=10321229.42), scope/filter giao rỗng, lọc loại không tồn tại, reload, SQL dành cho Builder và Viewer không có Agent/approval.
- Mở rộng loại widget bằng response fixture để kiểm card/line/bar/horizontal_bar/pie/donut/table; HTML label giữ nguyên text, không sinh `<img>`/JS; mô phỏng lỗi riêng by_month và HTTP 401 để kiểm UX. Các response fixture này chỉ chứng minh renderer/error UX, không thay bằng chứng API/DB.
- Money fixture `9007199254740993.17` hiển thị `9.007.199.254.740.993,17`; card/table dùng BigInt/chuỗi để chỉ làm tròn khi hiển thị. Chart chuyển sang number chỉ để vẽ; dữ liệu gốc và bảng chi tiết vẫn giữ nguyên chuỗi Decimal.
- Screenshot desktop/mobile đã kiểm tra trực quan tại `frontend/test-results/dashboard-Viewer-CA-opens--95545-ters-empty-scope-and-reload/`; viewport 390px không scroll ngang, chart resize theo container.
- Contract bổ sung chặn layout tràn/lấn nhau, card lấy nhầm grouped query, chart thiếu dimension và decimal NaN/Infinity.

### Phần còn lại và blocker hiện tại

Blocker credential cho **DB test** trong mục 12 được giải quyết bằng script tự tạo cluster; các dòng lịch sử không còn phản ánh trạng thái DB test mới. P0-T01 vẫn thiếu credential chỉ đọc tới **DB chính**; không đánh dấu audit đó hoàn tất. Chưa triển khai LangGraph/HITL phase 3–4; chưa có key/model/pricing live, chưa đạt live evaluation/latency/budget gates, chưa có backup/restore runbook bàn giao đầy đủ phase 5. MVP tổng thể **chưa hoàn tất**.

## 15. P3-T01 — adapter, mock và prompt structured output

**PASS phần triển khai và HTTP mô phỏng**. `openai==3.31.0` đã pin trong pyproject/lock, `pip check` đạt. `backend/tests/unit/test_llm_adapter.py` có **21 pass**; toàn bộ unit/static/regression sau cập nhật có **143 pass**. Không có call LLM thật trong các test này.

Adapter dùng Responses API với JSON schema strict; Pydantic kiểm lại cấu trúc và tính loại trừ plan/clarification/unsupported. Schema chuyển discriminated filter `oneOf` sang `anyOf` có cùng các literal phân biệt, đặt required đầy đủ, bỏ default/discriminator; semantic validation tiếp tục ở backend. Prompt chỉ gửi catalog metadata/current spec và thời gian request Asia/Ho_Chi_Minh, không gửi raw dataset/query results, credential, role hoặc scope. Adapter không import engine hay executor.

`LLM_MODE=mock` là mặc định và không cần key. Mock có một tập quy tắc tạo mới đơn giản phục vụ phát triển; yêu cầu mơ hồ/ngoài catalog/ngày tương đối chưa hỗ trợ trả clarification/unsupported. Mock không phải model và chưa được tính vào chất lượng LLM thật. Live yêu cầu provider, HTTPS endpoint, model ID, SecretStr key, input/output pricing, nguồn giá và ngày xác minh; thiếu một phần bị chặn trước network.

HTTP transport mô phỏng xác nhận SDK không tự retry (`max_retries=0`), request không có tool hoặc temperature, `store=false`, có max output tokens và reasoning effort; timeout có deadline asyncio. Case 429/500/401, JSON sai, refusal, incomplete và usage thiếu được xử lý riêng, không echo provider body/key. Usage output đã gồm reasoning tokens nên không cộng đôi. Call lỗi vẫn giữ usage nếu có; thiếu usage giữ `None`, không coi là miễn phí. OpenAPI/TypeScript đã regenerate, typecheck/lint đạt.

Tham chiếu giao thức: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses) và [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create). Live smoke/evaluation chưa chạy vì chưa có cấu hình provider/key/pricing; adapter chưa nối vào endpoint hội thoại, chờ P3-T03 graph. Budget reservation đã có ở mục 16.

## 16. P3-T02 — usage và dự trù ngân sách trước mỗi call

**PASS phần service và DB test thực**, chưa phải bằng chứng chi phí/latency của provider live. Migration `0003_usage_calls` tạo sổ per-call trong `app`, không thay đổi nguồn. Thêm bảng thứ 10 trong metadata app; các bảng/checkpoint khác giữ nguyên.

`UsageService.reserve()` khóa row Builder, kiểm owner của run, calls/tokens/deadline/chi phí run và tổng chi phí ngày trước khi commit reservation. Khóa cùng Builder được dùng cả reserve và reconcile; nhiều hội thoại không thể tranh hạn mức ngày. Đóng transaction trước khi gọi planner/network. Mỗi call có ID riêng và giá được snapshot, usage reconciliation idempotent; missing usage/timeout/crash giữ toàn bộ reservation chứ không giải phóng ngân sách chưa xác định.

Input reserve tính bytes UTF-8 của prompt/context/schema/instructions cộng allowance framing; output reserve là max_output_tokens (gồm reasoning). Đây là ước tính bảo thủ, actual usage được đối chiếu sau call. Giá cấu hình phải bao phủ rate cao nhất áp dụng cho model/tier/cache write/region; báo cáo vẫn là estimated cost, không phải hóa đơn. Provider báo usage vượt reservation sẽ được ghi nhận rồi trả lỗi, không tiếp tục tạo preview từ call đó.

Hard caps 3 calls/24.000 token/60 giây, default budget 0,10 USD/run và 1 USD/ngày/Builder, ngày Asia/Ho_Chi_Minh. Một vòng sửa output sai; SDK retry tắt và mọi retry đều đi qua reserve mới. Deadline asyncio bao cả call và trừ thời gian sau khi reserve. Service cũng kiểm Builder đang hoạt động, không dựa vào prompt.

Kiểm chứng mới nhất: **150 unit/static/regression pass**, **45 integration pass không skip** (2 migration + 43 auth/RLS/executor/dashboard/budget), artifact `backend/.runtime/postgres-test-ve55kszn/`. Riêng budget có 7 integration test: 8 thread cùng Builder chỉ 2 reservation được phép khi ngày chỉ đủ 2; đối soát lặp không trừ hai lần; missing usage giữ chi phí; biên calls/tokens/run budget/60 giây; qua nửa đêm Việt Nam; một repair/ba retry; mock thành công, overrun và reservation khác owner bị chặn. `engine.pool.checkedout()==0` được kiểm tại thời điểm adapter bắt đầu.

Tại thời điểm mục 16, P3-T03 và Builder/evaluation còn đang triển khai; cập nhật mới xem mục 17–18. Không đánh dấu M3 hoặc MVP hoàn tất khi chưa có live quality gate.

## 17. P3-T03 và phần UI/evaluator của P3-T04

**P3-T03 PASS bằng mock + DB thật**: graph context → plan (usage-guarded) → validate → compile → execute → preview chạy với `langgraph==1.2.15`. SDK retry và graph retry side effect không tự bật. Không xuất tracing tới LangSmith; state không chứa credential/client/engine. Compiler/semantic validator kiểm toàn bộ query trước thực thi. Thread/run/scope/user ID do server quản lý, không lấy từ body/prompt.

API create/get/messages đều kiểm Builder và ownership. `0004_conversation_runs` thêm active run ID/time và draft scope. Bắt đầu/kết thúc lượt dùng transaction ngắn, không giữ transaction metadata trong lúc gọi provider/query. Lượt đồng thời thứ hai và revision cũ trả 409; Viewer 403 có reason code rõ; Builder khác 404. Không tạo DashboardVersion từ graph. Tài khoản đổi scope/role trong lúc chạy không nhận kết quả cũ; GET checkpoint/preview cũng loại dữ liệu không cùng scope. API trả `Cache-Control: no-store`.

Builder UI có chat, preview/ECharts, SQL/nguồn KPI, lỗi/quota, mock banner, permalink hội thoại và restore qua GET. Viewer không có link Agent và bị chuyển khỏi Builder. SQL chỉ là chuỗi truy vết, không được đưa từ output graph vào execution. Screenshot desktop/mobile đã xem trực quan, sửa grid/axis labels để cột ngang không bị chồng nhãn.

Evaluator chạy corpus qua login session thật và API graph, so KPI/dimension/filter/time/sort/limit/widget/authorization, rồi đối chiếu số liệu bằng SQL trực tiếp public tables độc lập compiler. Không chấm pass chỉ vì query chạy được. Câu hỏi/refusal text được so normalized theo oracle một cách bảo thủ; paraphrase có thể bị fail và cần review. Case phase 4 báo BLOCKED trong sales pre-check, không cộng pass. Report riêng mode/provider/model/usage/latency/plan diff/results và release gate.

Mock evaluation ngày 15/10/2026: **5 PASS, 18 FAIL, 7 BLOCKED**, artifact `backend/.runtime/postgres-test-4mliyg34/mock-sales-evaluation.json`. Đây là kết quả thật của mock giới hạn, không phải chất lượng LLM. Sales corpus chỉ có 14 valid; ngưỡng ≥18/20 cần full 20 valid sau phase 4. Không đổi prompt oracle hoặc threshold để ép mock pass. **P3-T04 và M3 chưa đạt live acceptance** vì chưa có provider/key/pricing thật.

## 18. P4-T01 — edits, giữ ID và cache trong hội thoại

**PASS deterministic/API/browser**, LLM chain chưa chạy nên task chưa đóng hoàn toàn. Khi có current draft, adapter chỉ được trả bounded patch (set/remove/clear filters, set dates/chart, add/remove/rename widget hoặc rename dashboard); backend áp dụng lên bản đã validate. Full replacement cho một draft đang có bị từ chối. Không có thao tác SQL hoặc đổi quyền. Tạo mới và chỉnh sửa dùng schema wire riêng, prune các definition không dùng để giữ token budget; Pydantic vẫn kiểm toàn bộ output sau API.

Fingerprint cache gồm plan/schema/catalog/scope; dữ liệu chỉ lấy từ checkpoint cùng conversation/thread/owner và hash draft hiện tại. Không cache lỗi query, không chia sẻ giữa người dùng; presentation-only giữ nguyên query thì không gọi DB analytics lại. History gửi provider chỉ tối đa 8 message/2.000 ký tự và không kèm query results. Preview trước đó được giữ khi clarification nếu quyền còn khớp.

Chuỗi test: năm 2025 ALL=97 → California=15 (4 query) → đổi cột ngang (0 query, reuse 4) → thêm sold properties=7 (1 query, reuse 3) → bỏ chart bang (0 query) → đổi năm 2024 (3 query, độc lập đối soát DB) → reset filter/date (ALL=1000). ID/nội dung/layout widget không liên quan giữ nguyên; stale revision không ghi đè; scope chuyển ALL→TX buộc chạy lại 4 query và trả 23 giao dịch 2025 TX. “Biểu đồ này” không rõ trả clarification, không query và giữ draft/preview cũ.

Kiểm chứng tới mục này: **156 unit/static/regression pass**, **56 integration pass không skip** (2 migration + 54 auth/RLS/executor/dashboard/budget/agent/edit), **6 Playwright pass**. `pip check`, typecheck/lint và build có `/builder` đạt. Browser artifact gần nhất `backend/.runtime/postgres-test-4mliyg34/`; ảnh `frontend/test-results/builder-Five-edits-preserv-87a54-lts-on-presentation-changes/builder-five-edits.png`.

**Tại thời điểm mục 18:** graph chưa có durable checkpointer/interrupt. P4-T02 đã được bổ sung và kiểm chứng ở mục 19. P4-T03 approval, P4-T04 đủ KPI, phase 5 và audit DB chính vẫn còn. Goal/MVP tổng thể tiếp tục mở.

## 19. P4-T02 — PostgreSQL checkpoint và restart process thật

**PASS trên DB test riêng, không dùng provider live.** `PostgresConversationSaver` triển khai protocol sync/async của `BaseCheckpointSaver` với SQLAlchemy/psycopg đang có. Migration `0005_graph_checkpoints` thêm bảng pending writes và process epoch. Runtime không tạo DDL. Checkpoint/metadata dùng typed JsonPlus, tắt pickle fallback; mỗi thao tác kiểm Builder active, owner, conversation và server-bound thread trước deserialize. Preview app và graph có namespace riêng. Pending writes giữ lần ghi đầu cho index thường, cập nhật index dành riêng theo protocol LangGraph; test kiểm cả parent, list/filter/before và retry.

Graph checkpoint đồng bộ trước khi tiến bước, interrupt tại `await_approval`. Node chờ duyệt không có side effect. Service chỉ giữ transaction ngắn; pool không có connection đang checkout khi chờ người dùng. Timeout run mặc định 120 giây. MVP chạy một backend process: epoch khác sau restart cho phép nhận run cũ; cùng epoch chỉ nhận run quá timeout cộng 30 giây. Đây chưa phải cơ chế lease cho deployment nhiều worker.

`tests/integration/test_checkpoint_recovery.py` có **6 test**: dừng/khởi động uvicorn thật tại awaiting approval giữ nguyên toàn bộ ConversationView, thread và draft; graph vẫn có interrupt và resume không gọi plan/query. Child process thoát đột ngột ngay sau begin, sau reserve, hoặc sau graph preview nhưng trước metadata finish; backend mới báo lỗi có kiểm soát hoặc khôi phục đúng preview (97 giao dịch năm 2025). GET lặp không thêm assistant message/revision/usage; pending reservation vẫn giữ nguyên. Builder khác bị chặn trước deserialize; Viewer không đọc conversation. Protocol writes có test riêng.

Kiểm chứng ngày 15/10/2026: **156 unit/static/regression pass**, **62 integration pass không skip** (2 migration + 60 test auth/RLS/executor/dashboard/budget/agent/edit/recovery). Artifact: `backend/.runtime/postgres-test-r6tl4nmq/`. PostgreSQL test đã dừng sau chạy. Các test resume ở mục này chỉ kiểm internal graph marker; chưa có approval API và chưa chứng minh publication/retry sau commit. P4-T03 vẫn mở, MVP chưa hoàn tất.

## 20. P4-T03 — Duyệt, từ chối, publication và retry sau crash

**PASS trên DB test riêng và mock deterministic.** `POST /api/conversations/{id}/approval` yêu cầu decision/revision/hash/base version chính xác. Auth/Origin, Builder active, ownership và scope được kiểm trước checkpoint; khóa theo User → Conversation → Dashboard. Draft đang chạy, hash/revision/base cũ, scope thay đổi hoặc graph không chờ đúng run đều không được phát hành. Base conflict có mã `base_version_conflict` để UI hướng dẫn mở phiên bản mới nhất và bắt đầu hội thoại mới.

Một transaction lưu immutable version, current-version pointer, receipt, conversation status/message và audit. Reject chỉ lưu quyết định, không tạo dashboard/version. Receipt nằm trong namespace `approval`, khóa theo revision/content hash của conversation/thread; khác quyết định trên cùng draft trả 409. `DashboardVersion.draft_hash` là hash định danh draft có conversation/revision/content/base/decision (cũng là approval key), còn hash nội dung chuẩn nằm trong conversation, request và audit. Nhờ đó retry không tạo trùng nhưng một lần sửa sau này có thể chủ động trở về nội dung cũ và tạo phiên bản mới. Không thay uniqueness constraints hay dữ liệu nguồn.

Sau commit mới resume node thuần `await_approval`. Mất response hoặc process chết giữa commit/resume: retry lấy cùng receipt/dashboard/version và chỉ hoàn tất graph. Nếu graph/ack chưa xong, API trả `checkpoint_synced=false` cùng kết quả đã commit; UI cho hoàn tất lại, chặn edit cho tới khi đồng bộ xong. Mất ack sau khi graph đã kết thúc cũng không replay query/LLM. Chỉnh tiếp sau approve dùng đúng version vừa được duyệt làm base; competing publish vẫn bị chặn 409.

9 test trong `test_approval.py` kiểm new publication/Viewer CA=15 trong khi preview ALL=97; reject lặp giữ version cũ; stale revision/hash/base/edit; revert về content hash cũ; hai conversation tranh base; bốn approval đồng thời chỉ tạo một version và một audit; RBAC/Origin/strict body/scope; child process thoát ngay sau commit rồi uvicorn mới retry; graph xong nhưng mất ack; audit failure sau version flush rollback cả dashboard/version/receipt. Các test không gọi provider thật.

Đã sửa một race được phát hiện khi chạy regression: LangGraph sync durability chờ checkpoint chính nhưng pending writes vẫn có thể đang dùng connection khi planner bắt đầu. Saver đăng ký pending writes ngay lúc được gọi và drain chúng trước `aput`; test cố ý chặn write chứng minh provider chưa được gọi, rồi kiểm `pool.checkedout()==0` khi provider chờ. Bộ recovery hiện có 7 test. Không dùng sleep để che race hoặc bỏ assertion connection.

Builder UI có approve/reject, receipt/link phiên bản đã duyệt, retry pending và thông báo xung đột. Playwright chạy backend thật: làm mất HTTP response sau publication, retry cùng phiên bản, reload khôi phục receipt, Viewer giữ version 1 trước duyệt rồi thấy version 2 khi bấm **Tải lại dữ liệu**, stale conversation bị chặn, reject không có publication và vẫn sửa tiếp được. Dashboard page đối chiếu số version của query result với spec trước render; nếu publish chen giữa hai request, tải lại metadata/query có giới hạn, không ghép dữ liệu mới với spec cũ.

Kiểm chứng ngày 16/10/2026: **156 unit/static/regression pass**, **72 integration pass không skip** (2 migration + 70 test còn lại), **8 Playwright pass**; typecheck/lint/build và `pip check` pass. Command: `python backend/scripts/test_local_postgres.py --e2e` trong venv. Artifact cuối: `backend/.runtime/postgres-test-clhpb208/`; ảnh trong `frontend/test-results/builder-Approval-survives--75ff7-s-scoped-versions-to-Viewer/` gồm `approval-preview.png` và `approval-published.png`. P4-T03 hoàn tất; offers/repairs, phase 5, LLM live và audit DB chính còn mở, không coi MVP đã nghiệm thu.

## 21. P4-T04 — Bảy KPI, tách fact và catalog compatibility

**PASS phần triển khai và deterministic regression; task vẫn mở ở live quality gate.** Catalog `1.1.0-offers-repairs` bật `offer_count`, `accepted_offer_rate`, `repair_cost`. Ngày lần lượt là offer_date/repair_date; rate cast numerator sang numeric trước chia, chỉ đếm status `accepted` đúng chữ thường. Không thêm view/migration vì `analytics.offers` và `analytics.repairs` security-invoker/barrier, grants và RLS đã có từ migration 0002.

Compiler/validator có allowlist riêng cho từng nguồn, ngày, khóa fact, cột và projection AST. Không cho query trộn KPI khác fact; offers/repairs không nhận agent dimension/filter. Executor thêm count khóa fact ẩn, phân biệt no_data với count/rate 0 hoặc SUM null mà không trộn transaction_count vào fact khác. Kết quả tiền và tỷ lệ vẫn là Decimal string; count là integer. UI card/bảng hiển thị tỷ lệ phần trăm bằng dịch dấu thập phân dạng chuỗi, không nhân tiền/tỷ lệ bằng float để hiển thị số chính xác. ECharts dùng giá trị numeric để vẽ, percent scale/tooltip cho rate; dữ liệu chính xác vẫn có ở bảng.

Catalog 1.0.0 chỉ cho bốn KPI sales cũ và tiếp tục compile thành cùng SQL/công thức. Dashboard/query phải cùng catalog version. Đọc/filter legacy không đổi version; một add_widget dùng KPI mới nâng draft và query metadata lên 1.1.0, giữ widget/nội dung cũ. Bản đã duyệt không đổi cho tới approval. Có integration test đọc dashboard legacy, sửa filter, thêm offers, rồi duyệt version 2. Cache vẫn chứa catalog/scope trong fingerprint.

53 integration test mới: 9 golden case offers/repairs (ALL/CA/TX, 2025, empty), 42 grouped case qua bảy dimension × ba scope × hai fact so với direct public-table SQL độc lập, một mixed-fact draft/approval/Viewer và một legacy-upgrade journey. Mixed-fact 2025 ALL: sales 81.983.427,35; offers 9 và accepted 2/9; repair 367.957,16. Viewer CA: sales 10.321.229,42; offers 2/rate 0%; repair 25.985,57. Sales không bị nhân khi thêm widget fact khác. 18 unit case mới kiểm date/hidden count, mixed fact, agent filter, legacy và SQL tampering; thêm một evaluator test kiểm mọi metric của query không gắn widget.

Evaluator full 30 case đã chạy: **8 PASS, 22 FAIL, 0 BLOCKED** bằng mock, release_gate_passed=false. Oracle/nội dung câu hỏi/threshold không đổi; chỉ version corpus được cập nhật. Kiểm số liệu thực thi tách riêng khỏi đúng ý câu hỏi: **22 query thực thi đều đúng theo actual structured plan**, đối soát từng metric kể cả query không gắn widget với SQL tham chiếu độc lập. Gate live yêu cầu thêm mọi execution_accuracy.all_correct; không cho ≥18 plan pass che mất query trả sai số liệu. Unexpected oracle blocker trả exit 2 ngay cả khi đồng thời có FAIL.

Kiểm chứng 16/10/2026: **175 unit/static/regression pass**, **125 integration pass không skip** (2 migration + 123 test còn lại), **9 Playwright pass**; lint/typecheck/build pass. Validator/fact-plan suite sau dọn hằng số thừa: 54 pass. Command `python backend/scripts/test_local_postgres.py --e2e --evaluate` trong venv; artifact `backend/.runtime/postgres-test-bwnyrb1h/`, report `mock-full-evaluation.json`. EXPLAIN đủ ba fact/scopes/grains: 75 plan, root max 3905.65, ngưỡng 10000 không đổi. PostgreSQL test đã dừng. Đã xem ảnh `frontend/test-results/builder-Mixed-facts-preser-8807b-formatting-and-Viewer-scope/mixed-facts-preview.png`; card 22,22% và tiền đúng. Browser còn kiểm published Viewer và empty 2030.

**Chưa đạt:** không có key/model/pricing thật nên chưa có kết quả live ≥18/20 valid và 10 case còn lại, five-edit live journey hoặc latency provider. Audit DB chính vẫn chưa chạy. Không đánh dấu P4-T04/M4/MVP hoàn tất từ các bằng chứng mock/test DB này; tiếp tục phase 5 và bổ sung live evidence khi cấu hình sẵn sàng.

## 22. P5-T01 và deterministic P5-T02 — Security matrix và phục hồi UI

**P5-T01 PASS trên DB test riêng; live M4 và MVP vẫn mở.** `test_security_matrix.py` thêm 25 integration case: Builder ALL/TX và Viewer CA truy cập workspace đã duyệt, giao filter CA/TX với scope trên cả ba fact, không trả cột PII/hidden count; Client không override scope/SQL/user/thread/dashboard qua query body; owner khác không đọc/sửa/duyệt, denial không phân biệt conversation thiếu và foreign; không có API raw checkpoint/thread. Origin thiếu/null/khác/duplicate bị chặn cho toàn bộ POST liên quan. Session demoted/disabled/expired được kiểm lại qua DB. Malicious provider trả SQL, identity override, PII dimension, mixed fact hoặc invalid agent dimension đều bị chặn trước executor.

`test_adversarial_plans.py` thêm 18 unit case: dict/model_construct không bypass schema/semantic validation; filter SQL/HTML payload vẫn là bind data. Test phát hiện Pydantic serializer warning có thể echo giá trị model sai dạng trước validation. Compiler dùng dump không phát warning rồi revalidate, vẫn fail closed; regression kiểm không có warning. Lỗi DB và provider tiếp tục trả message/code cố định, không raw SQL/credential/exception. Provider-failure sentinel không xuất hiện trong log backend của lần chạy cuối.

Runner lấy row count và content MD5 của mọi dòng trong **16 bảng source public**, trước migration và sau toàn bộ auth/API/browser/fault tests. Sắp hash theo nội dung để không phụ thuộc thứ tự physical rows; artifact chỉ chứa tên bảng, số dòng, digest. Ledger `public.alembic_version` do Alembic quản lý được loại riêng vì không thuộc import source; không loại bảng source nào. `source-integrity.json` của lần cuối có unchanged=true; không suy kết quả này thành audit DB chính. Một lần chạy đầu phát hiện ledger mới như bảng thêm; điều chỉnh phạm vi fingerprint đúng source, toàn bộ 16 bảng gốc ở lần đó cũng không đổi.

**P5-T02 deterministic PASS; task chưa đóng vì thiếu live smoke.** Ba file `full-journey.spec.ts`, `permissions.spec.ts`, `recovery.spec.ts` bổ sung: chuỗi năm edit giữ ID → approve → Viewer CA filter giao TX ra empty; chart rate và bảng đúng 22,22%; foreign Builder/Viewer bị chặn bằng session riêng; uvicorn restart thật giữa preview/approval giữ draft/hash/messages và approval thành công; HTTP 503 giữ draft/input cho retry. Provider failure dùng ASGI factory riêng chỉ chạy khi APP_ENV=test/LLM_MODE=mock; exception nội bộ không tới response/log, preview trước đó được giữ và request sau khi khôi phục thành công. Đây không phải provider thật/live smoke.

Restart/fault được điều khiển bằng file JSON private trong artifact directory và thread runner, kiểm action/UUID; chỉ dừng/khởi động child process đã tạo. Không thêm route/env backdoor vào `app`. Reader outage dùng cổng loopback trống, tạo connection failure thật trong psycopg; không giả kết quả RLS hoặc stop database nguồn. Test đầu đã phát hiện connect chưa có timeout nên UI nhận lỗi trang chung. Đã thêm timeout kết nối và pool checkout mặc định 3 giây, hard 2–5; audit principal reader cũng có SET LOCAL statement_timeout 5 giây. Sau sửa, UI nhận lỗi từng widget, nút reload vẫn hoạt động, restart về cấu hình đúng trả lại 135 giao dịch CA. Integration resource test với socket bound nhưng không listen chứng minh connection failure <6 giây, không lộ password và pool checkedout=0. Chưa dùng test này để kết luận P95.

Kiểm chứng 16/10/2026: **195 unit/static/regression pass không warning**, **151 integration pass không skip** (2 migration + 149 test), **16 Playwright pass**. Artifact cuối `backend/.runtime/postgres-test-ihn0gbrv/`, `source-integrity.json` unchanged=true. Command trong venv: `python backend/scripts/test_local_postgres.py --e2e`. Lint/typecheck đạt; build kiểm lại sau đổi nhãn cột bảng sang tiếng Việt. Đã xem ảnh `reader-outage.png` trong recovery result và `offer-rate-chart.png` trong full-journey result. Runtime table labels dùng tên KPI/dimension tiếng Việt, API IDs không đổi.

**Còn mở:** provider/model/key/pricing live, audit DB chính, smoke live, benchmark ≥30 mẫu mỗi nhóm, P95 và runbook/restore clean setup. Goal vẫn active; không có kết luận MVP hoàn tất.

## 23. Validator review, benchmark đầu và backup/restore — 16/10/2026

Đã đóng hai finding validator còn tồn từ review: `ORDER BY` chỉ nhận null ordering mặc định PostgreSQL (ASC NULLS LAST, DESC NULLS FIRST); equality token `==` bị từ chối trước SQLGlot normalization, trong khi `=` và comment chứa `==` vẫn hợp lệ. Không đổi compiler/catalog hoặc semantics bảy KPI. Astra re-review không còn finding: 55 targeted tests cùng 12 tổ hợp compile ba fact/hai chiều sort/hidden fact count đạt; placeholder keys khớp chính xác.

Benchmark chạy `python backend/scripts/test_local_postgres.py --benchmark`, dùng PostgreSQL 16.9 test riêng, Next.js production, Chromium 156, Python 3.11.9, Windows AMD64, 12 logical CPU và RAM khoảng 15,2 GiB. Dataset: 1.000 sales, 125 offers, 125 repairs; catalog 1.1.0. Warm-up 3 lượt/nhóm được loại riêng; mỗi nhóm có 30 mẫu đo từ navigation/send tới DOM/canvas sẵn sàng và hai animation frame. P95 nearest-rank trên mẫu thành công, mọi lỗi vẫn được giữ và làm gate fail.

| Nhóm | Thành công / mẫu | P95 request-to-render | P95 API | P95 DB | Lỗi |
|---|---:|---:|---:|---:|---:|
| Dashboard đã lưu, Viewer CA | 30/30 | 332,902 ms | 104,431 ms | 58 ms | 0 |
| Tạo dashboard, Builder ALL, mock | 30/30 | 475,516 ms | 300,648 ms | 68 ms | 0 |

Artifact `backend/.runtime/postgres-test-kis118f8/benchmark-mock.json` ghi samples/warmups, usage, cấu hình và thời điểm. Có integration workload chạy chồng trên cùng máy; số này là thăm dò có giới hạn, chưa phải baseline độc lập. Provider/model/pricing trong report là null và `live_performance_gate_passed=false`; không dùng mock để nghiệm thu P95 AI thật. Cùng lần chạy: 2 migration + 149 integration pass; 75 EXPLAIN max 3905.65; fingerprint đủ 16 bảng không đổi; cluster dừng sạch.

`scripts/restore-db.ps1` kiểm manifest SHA-256, target có tên test và rỗng, attestation separate cluster và known source endpoint. Source URL có query/fragment bị từ chối vì có thể override endpoint. Restore single-transaction, không clean/overwrite, không owner/ACL; lỗi rollback và trả môi trường về caller. Cả backup/restore xóa tạm PGHOSTADDR/PGSERVICE/PGSERVICEFILE để endpoint thực tế không bị libpq environment đổi hướng. Backup đặt cả setup output trong try/finally. Runner cũng bọc các kết nối psycopg/SQLAlchemy trong restoring environment context.

Đã chạy thực sự `--restore-only` với PGHOSTADDR trỏ IP không định tuyến và PGSERVICE không tồn tại ở cả process runner và subprocess script. Cả backup và restore wrapper thành công; SHA-256 hợp lệ, fingerprint source/target giống nhau trên 16 bảng, restore lần hai bị chặn và cluster dừng. Artifact cuối `backend/.runtime/postgres-test-tbdll2vc/` có `restore-verification.json` và `source-integrity.json` unchanged=true. Lần full integration dùng restore wrapper trước đó cũng đạt 151 test: `backend/.runtime/postgres-test-vim9f0fp/`. Các test guard riêng xác nhận từ chối target production, same cluster, source override URL, checksum sai và thiếu confirmation; không echo password. Có regression môi trường được phục hồi khi setup thất bại.

README bổ sung tạo venv cho setup mới; runbook có backup/restore, giới hạn hostname alias/role provisioning, lệnh regression/benchmark và phân biệt mock/live. **P5-T03/P5-T04 vẫn mở:** cần baseline hiệu năng không workload cạnh tranh, provider thật, clean-setup/demo cuối và audit DB chính. Không đánh dấu goal/MVP hoàn tất.

Kiểm tra cuối sau hardening môi trường: **210 unit/static pass**, `pip check` đạt; Astra không còn finding ở validator/backup/restore. Kiểm tra sự hiện diện cấu hình (không in giá trị) xác nhận chưa có các biến provider/model/key/pricing; audit DB chính không khởi tạo kết nối vì không có URL local trong cấu hình đang đọc. Bằng chứng test cluster không được dùng thay cho hai phần thiếu này.

Baseline lần hai chạy sau khi các regression process khác đã kết thúc: `backend/.runtime/postgres-test-b3z884nv/benchmark-mock.json`. Cùng cấu hình production/3 warm-up/30 measured samples: saved Viewer CA P95 **317,956 ms** (API 102,689 ms; DB 53 ms), mock Builder ALL P95 **438,702 ms** (API 268,834 ms; DB 61 ms); cả hai nhóm 30/30 thành công, không lỗi. Tất cả regression của runner hoàn tất trước khi build/đo; không chủ động chạy workload test cạnh tranh trong lần đo này. Không khẳng định máy hoàn toàn không có background workload của hệ điều hành. Backup/restore wrapper với hostile libpq environment, 151 integration test, 75 EXPLAIN và fingerprint 16 bảng đều đạt ở chính lần chạy này; cluster và app process đã dừng. `live_performance_gate_passed=false` vì AI vẫn mock; phần baseline độc lập đã có, phần provider thật vẫn chờ cấu hình.

## 24. P5-T04 — diễn tập setup sạch từ source hiện tại

Thêm `backend/scripts/verify_clean_setup.py --e2e`. Script dùng danh sách git-visible source, gồm nội dung thay đổi chưa commit, để tạo snapshot riêng trong `.runtime`; loại env thật, dependency, git metadata và runtime. Tạo virtualenv mới, cài requirements.lock và npm ci, copy env example, regenerate API, chạy unit/static, lint, production build, typecheck, rồi isolated PostgreSQL/browser runner. Chỉ process test và cluster do runner tạo được quản lý; không dùng DB chính. Mỗi bước ghi log và exit code trong `setup-report.json`; artifact được giữ để kiểm tra.

Artifact chạy: `backend/.runtime/clean-setup-40qczutk/`. Toàn bộ 11 bước exit 0: tạo venv; cài Python lockfile; pip check; npm ci; generation; **210 unit/static pass** trong snapshot; lint; production build; typecheck; Chromium install; PostgreSQL/browser. Runner DB bên trong snapshot đạt **151 integration pass không skip**, 75 EXPLAIN max 3905.65 và restore/fingerprint đối soát source-target trước migration. **16 Playwright pass** gồm approval, Viewer scope, năm edit, backend restart thật, reader outage và provider fault test-only. `project/backend/.runtime/postgres-test-qr40_vym/source-integrity.json` ghi unchanged=true cho 16 bảng; cluster/app process được dừng sau test. Thời gian từng bước và log riêng nằm cùng setup-report, không chỉ dựa vào thông báo script thành công.

Review Astra tìm thấy inherited PIP_TARGET/PREFIX/config có thể đổi đích cài đặt. Đã sửa helper để bỏ installation destination và Python startup overrides; dùng PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV=true, PYTHONNOUSERSITE=1, giữ registry/proxy policy bằng env. Hai regression test riêng đạt, trong đó gọi pip config thật với file cấu hình và target cố tình sai; Astra re-review không còn finding. Lần snapshot đang chạy bắt đầu trước bản sửa helper; kiểm tra xác nhận môi trường và pip config của lần đó không có destination override. Hai test hardening chạy trên source hiện tại, không cộng lẫn vào con số 210 của snapshot. Wheel/npm/Chromium cache có thể được dùng lại: đây là dependency/venv mới trên cùng Windows, không phải chứng minh trên OS/máy mới.

`npm ci` phát hiện full dependency audit **5 high**, cùng một chuỗi dev-only: eslint-config-next → plugin-next → fast-glob → micromatch → braces. Production audit `npm audit --omit=dev` vẫn **0**. [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) ảnh hưởng braces <=3.0.3, chưa có patched version tại thời điểm đối chiếu registry/advisory. Không force downgrade eslint-config-next 16 về 14 theo đề xuất audit; runbook ghi phạm vi lint với trusted patterns, theo dõi bản vá tương thích. Đây là risk còn mở của dev tooling, không tuyên bố full dependency tree sạch.

README đã sửa đoạn cũ ghi chưa có publication: hiện hướng dẫn preview/diff rồi approve/reject đúng chức năng đang có. P5-T04/MVP vẫn mở vì thiếu live-provider gates, audit DB chính và nghiệm thu cuối; không dùng kết quả setup mock để đóng các yêu cầu này.

## 25. Lần nghiệm thu LLM thật đầu tiên — 17/10/2026

User đã cấu hình key và chọn `gpt-6-luna`. Read-only models.retrieve xác nhận quyền truy cập; kiểm tra này không phải generation smoke. Đối chiếu [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna): Standard input 0.10 USD/1M, cache-write 0.125, output 0.50; cấu hình reservation dùng input 0.125 để bao phủ cache-write, output 0.50, verified 17/10/2026. Ước tính này có thể cao hơn hóa đơn thật. Adapter truyền `service_tier=default` để chọn Standard rõ ràng, thay vì kế thừa project auto tier; 21 test adapter pass sau thay đổi. Key không nằm trong command line/report/git; `.env` mặc định vẫn mock.

Runner có `--live-evaluate` opt-in; preflight provider/pricing trước tạo cluster, chỉ subprocess evaluator chuyển sang live. Default regression/mock/browser không đổi. Sáu test runner pass; agent triển khai chạy unit directory 185 pass. Lệnh thực tế: `python backend/scripts/test_local_postgres.py --live-evaluate`; artifact `backend/.runtime/postgres-test-jv5ddczz/live-full-evaluation.json`. Trước live: 151 integration pass không skip, 75 EXPLAIN max 3905.65; restore đúng fingerprint 16 bảng. Sau live: source-integrity unchanged=true và cluster đã dừng. Global mode không tự bật live.

**Raw live result: 12 PASS / 18 FAIL / 0 BLOCKED, release_gate_passed=false.** Theo category: valid 11/20, ambiguous 0/5, unsupported 1/5. Tổng 29 provider calls, 72.225 token input+output, estimated cost 0.012419 USD. Case Viewer bị auth chặn trước provider. Có 27 query đã thực thi; mọi execution_accuracy.all_correct=true theo SQL tham chiếu độc lập cho actual plan. Điều này không chứng minh plan đúng ý người dùng: bảy valid case dùng tên bang California/Texas thay mã CA/TX, sáu case trả rỗng sai ý. Thiếu canonical state-value metadata trong prompt là lỗi thực tế cần sửa.

Các khác biệt khác: valid_001 thiếu default overview cards và có sort thêm; valid_002 thiếu oracle sort; valid_019 chọn horizontal_bar khi oracle bar. Năm ambiguous đều clarification/no-query, bốn case chỉ lệch câu chữ; một case có thêm reason code. Bốn unsupported từ chối đúng reason/no-query nhưng không chứa đúng các cụm oracle; report đầu chưa giữ message nguyên văn, nên chưa đủ bằng chứng kết luận nội dung đúng về ngữ nghĩa. Giữ nguyên raw report, oracle, threshold và gate false; không đổi điểm để ép đạt. GPT-6-Sol Medium đã chẩn đoán, đang sửa canonical state guidance/normalization chỉ với decision mới và bổ sung message vào report để lần tiếp có thể kiểm tra đầy đủ. Chưa tick các task live từ lần thất bại này.

## 26. Live evaluation lần hai và chuẩn bị browser live — 17/10/2026

Artifact `backend/.runtime/postgres-test-b_c1_5o4/live-full-evaluation.json`: **11 PASS / 19 FAIL**, gồm valid 10/20, ambiguous 0/5, unsupported 1/5; gate vẫn false. Có 29 provider calls, 75.460 input + 9.061 output tokens, estimated cost 0.013977 USD theo reservation rate; không phải hóa đơn. Cả 30 query thực thi đều `execution_accuracy.all_correct=true`. Runner đạt 151 integration test trước evaluation; source integrity đủ 16 bảng unchanged=true và cluster đã dừng.

Tên bang đã canonicalize đúng CA/TX; overview và hướng bar đã đúng. Mười valid case còn fail đều thiếu sort, trong đó valid_006/012 có thứ tự hàng sai mong đợi. Đây không chỉ là khác câu chữ: trục thời gian không được xếp tăng dần là lỗi hiển thị thực tế. Đã thay hướng dẫn blanket “không sort nếu không yêu cầu” bằng quy tắc chung: mọi chart/table thời gian sắp tăng dần, bar phân loại mặc định KPI giảm dần, ưu tiên thứ tự người dùng chỉ định. Không thêm ngoại lệ riêng để khớp oracle overview vốn mong sort rỗng; giữ nguyên oracle/grader và ghi rõ xung đột này khi đánh giá tiếp.

Report mới giữ actual message/reason để review: ambiguous_002 còn thiếu hỏi khoảng thời gian và reason `missing_time_range`; đã bổ sung hướng dẫn kiểm đủ KPI/chiều so sánh/thời gian. Các clarification khác vẫn hỏi lại, không query, nhưng lệch exact wording. Unsupported từ chối đúng loại và không query; unsupported_002 giải thích thiếu hoa hồng nhưng chưa nêu caveat lịch sử đại lý/fan-out mà oracle mong đợi. Không quy đổi manual review thành PASS. Prompt/state-focused suite đạt 36 test; Astra review state normalization không có finding.

Thêm các opt-in riêng `--live-smoke` (6 lượt planner trong browser, không retry) và `--live-benchmark` (30 samples/nhóm + 3 warm-up, production frontend); mặc định app/regression vẫn mock. Review Astra phát hiện reporter ghi đè làm mất attachment trong bộ live và phép thử CA-saved AND TX không đủ phân biệt scope. Đã giữ configured live reporters, ghi evidence JSON thành file, bổ sung control trên dashboard seed không filter: ALL+TX có dữ liệu, CA+TX không dữ liệu; sửa baseURL của Viewer context và ignore toàn bộ live reports/screenshots. 23 runner/benchmark tests và frontend typecheck đạt. Kết quả thực chạy browser/benchmark sẽ bổ sung riêng, không suy từ test definition.

Presence-only check cấu hình sau khi user báo đã điền: API key/model có; `RUNTIME_DATABASE_URL` và `MIGRATION_DATABASE_URL` vẫn trống. Chưa kết nối/audit hoặc migration DB chính của user; mọi DB test nêu trên là cluster riêng.

## 27. Live browser smoke và cấu hình audit DB chính — 17/10/2026

Lần `--live-smoke` đầu trên cluster `backend/.runtime/postgres-test-epcm30qw/` qua 151 integration regression rồi chạy Chromium thật. Initial planner tạo đủ 6 widget, draft 1 và SQL preview, nhưng chọn `bar` cho chart tháng; test yêu cầu `line` nên dừng ngay lượt đầu. 16 bảng nguồn vẫn unchanged=true và cluster dừng. Artifact lỗi/screenshot và `live-run-evidence.json` nằm trong `frontend/test-results-live/`; không coi journey năm edit hay Viewer là đạt. Prompt được làm rõ để day/month/quarter mặc định dùng line, đồng thời giữ khả năng bar cho năm hoặc khi người dùng chỉ định. Đang chạy lại trên cluster riêng.

User có tài khoản quản trị `postgres` nhưng chưa có role runtime/reader riêng. Thêm `AUDIT_DATABASE_URL` vào `.env.example` và cho `audit_db.py` đọc đúng trường này từ `backend/.env`, ưu tiên `--dsn`/process env; chỉ dùng cho audit `BEGIN READ ONLY`. `Settings` nhận URL dưới dạng `SecretStr` để backend khởi động với `.env` chung, không dùng credential audit làm runtime hoặc reader. 31 targeted tests pass. Lần audit đầu `backend/.runtime/main-db-audit.json` có status blocked: URL còn mật khẩu mẫu nên kết nối PostgreSQL thất bại. Đã hướng dẫn user thay mẫu bằng mật khẩu thật trong file; chưa khẳng định DB chính đã audit. Tài khoản superuser đọc được schema/privileges nhưng không chứng minh RLS của reader CA/TX; gate đó cần đúng các role riêng.

## 28. Audit DB chính, golden SQL, live browser và benchmark gate

Sau khi user thay mật khẩu mẫu, audit lại cùng lệnh và artifact `backend/.runtime/main-db-audit.json` đã thành công lúc `2026-10-16T21:08:34Z` (ngày 17/10 giờ Việt Nam): PostgreSQL 16.9, `real_estate_db`, transaction read-only, đủ 16 bảng nguồn/2.203 dòng/16 PK/16 FK/5 UNIQUE; không có duplicate hay FK orphan. Mọi chênh lệch schema, khóa, row count và baseline so với import đều rỗng. Baseline ALL 1.000 giao dịch/870.798.679,64; CA 135/107.064.313,73; TX 132/128.635.636,80. Role audit là superuser/BYPASSRLS; RLS bốn bảng nguồn analytics chưa bật trên DB chính. Đây không phải bằng chứng runtime/reader đã triển khai.

`backend/scripts/verify_golden_db.py` chạy ba prepared statement SQL độc lập từ fixture trên DB chính trong giao dịch chỉ đọc, timeout 5 giây: **15/15 case đạt**, gồm bảy KPI, ALL/CA/TX, năm 2025, kỳ rỗng và ngày biên. P0-T01/P0-T02 đã tick; chi tiết quyền, index, NULL/ngày và fan-out ở `docs/data-audit.md`. Không có DDL/DML trên DB chính.

Live smoke lần hai: `backend/.runtime/postgres-test-9zflz8bt/` đạt 149 integration và **1/1 Playwright live journey** trong 31,5 giây. Test đi qua sáu lượt planner (create + 5 edit), kiểm tra tái sử dụng query, duyệt, Viewer CA trên published dashboard và control unfiltered ALL+TX có dữ liệu/Viewer CA+TX không dữ liệu. Source integrity 16 bảng unchanged=true, cluster dừng. Artifact screenshot/HTML/evidence trong `frontend/test-results-live/` và `frontend/playwright-report-live/` (ignored). Task P4-T01/P5-T02 còn phụ thuộc các gate tổng thể khác, không tick chỉ từ journey này.

Lần đầu `--live-benchmark` trên `backend/.runtime/postgres-test-wgiuupnq/` **chưa vào pha đo**: 148/149 integration pass, `repairs-property_type-ALL` lỗi `database_error` sau 6.378 ms trong lúc máy chạy tải dài; source integrity unchanged=true, cluster dừng. Không có `benchmark-live.json`, nên P5-T03 và performance gate vẫn mở. Cần xác minh nguyên nhân và chạy lại trước khi kết luận độ trễ AI thật.

Lần chạy lại trên `backend/.runtime/postgres-test-j0gwt729/` đạt **149/149 integration**, build production xong và bắt đầu benchmark browser. Máy/môi trường tạm ngừng đủ lâu để Python `subprocess.run` hết timeout (exception báo deadline đã vượt khoảng 25.365 giây); app log cho thấy ít nhất hai request Agent đã thành công trước khi mất kết nối. Không có `benchmark-live.json`, nên không có P95 hợp lệ. Runner dừng app/cluster trong `finally`; fingerprint 16 bảng unchanged=true. Lần này không quy lỗi cho query SQL hay coi các request dở dang là mẫu hoàn chỉnh.

## 29. Migration trên PostgreSQL chính — 17/10/2026

User đã cấu hình `AUDIT_DATABASE_URL` trong file `.env` bị ignore; không đưa credential vào báo cáo. Audit chỉ đọc trên `real_estate_db` và đối chiếu golden SQL lại đạt **15/15**. Backup custom-format của chính DB này nằm trong thư mục `backups/real-estate-db-pre-migration-20261017/` bị ignore; SHA-256 archive là `0076f37a656c1b831ca1532ea484bee13d566334c2627464574f7cee2671c97e`, có manifest riêng và `pg_restore --list` kiểm tra được. Backup giữ dữ liệu/schema nhưng không phải bản sao đầy đủ role toàn cluster; preflight lưu riêng owner, ACL, default ACL và role membership ở `backend/.runtime/main-migration-preflight.json`.

Đã khôi phục backup vào **cluster PostgreSQL 16 tách biệt**, đối chiếu fingerprint nội dung của cả 16 bảng nguồn với DB chính, chạy lại audit/golden 15/15, rồi nâng Alembic tới revision cụ thể `0005_graph_checkpoints`. Sau diễn tập, fingerprint 16 bảng không đổi; report `backend/.runtime/main-restore-rehearsal-o8mkq8cr/rehearsal-report.json` ràng buộc SHA-256 archive, fingerprint nguồn, system identifier cluster nguồn và SHA-256 mã migration. Cluster diễn tập đã dừng. Preflight trên DB chính chỉ đọc, tám nhóm xung đột về schema/reader/RLS/PUBLIC/consumer đều bằng 0. Rà soát bảo mật sau các guard này không còn điểm chặn triển khai.

Wrapper triển khai kiểm tra lại chính backup, nội dung migration đã diễn tập, cluster và fingerprint nguồn; truyền admin URL chỉ trong process migration với lock timeout 5 giây, statement timeout 60 giây và không tự retry. **Migration DB chính đã thành công**: Alembic `0005_graph_checkpoints`, ba group reader `NOLOGIN`, RLS bật trên bốn bảng nguồn; 16 bảng nguồn giữ nguyên fingerprint. Audit hậu migration vẫn có 16 bảng/16 PK/16 FK/5 UNIQUE, không có chênh lệch schema/row count/baseline với import; golden SQL tiếp tục **15/15**. Artifact `backend/.runtime/main-migration-result.json` và `main-db-post-migration-audit.json` bị ignore.

Việc này **chưa phải bằng chứng ứng dụng đã chạy trên DB chính**: user mới có login `postgres`; chưa có login runtime/ba login reader không đặc quyền, chưa cấu hình bốn URL đó trong `.env`, chưa seed demo users. Không dùng `postgres` làm credential API hoặc reader. Live 30-case gate vẫn chưa đạt (lần gần nhất 20/30 raw, valid 19/20 nhưng ambiguous/unsupported chưa đạt), và benchmark live vẫn chưa có P95; do đó MVP/gate cuối vẫn mở.

Lần live evaluation thứ ba trước migration lưu ở `backend/.runtime/postgres-test-nvpbvyw3/live-full-evaluation.json`: **19 PASS/11 FAIL**, valid **18/20**, ambiguous **0/5**, unsupported **1/5**; 29 provider calls, 93.541 input và 11.724 output tokens, estimated cost 0,017567 USD. Cả 24 query được thực thi đều đúng theo SQL đối chứng cho actual plan, nhưng điều đó không biến các plan/clarification sai thành PASS. `valid_001` vượt trần 4.096 output tokens và lỗi sau khoảng 60 giây; `valid_004` chọn line thay vì bar cho chart năm. `unsupported_002` thực sự hỏi lại thay vì từ chối, các fail ambiguous/unsupported còn lại chủ yếu là khác exact wording của oracle. Sau đó đã tăng riêng trần output cho lượt tạo dashboard lên 6.144 và hướng dẫn chart năm/unsupported; 37 test prompt/adapter/usage đạt. **Chưa chạy lại 30-case sau thay đổi này**, không tự nâng điểm hay thay oracle.

Lần live evaluation thứ tư đã chạy sau thay đổi output/chart, trên cluster test `backend/.runtime/postgres-test-8iqb7fb6/`: 149/149 integration trước live, **20 PASS/10 FAIL**, valid **19/20**, ambiguous **0/5**, unsupported **1/5**; toàn bộ query có bằng chứng execution accuracy đều đúng, 16 bảng nguồn không đổi và cluster đã dừng. `valid_001` nay trả plan trong khoảng 9 giây nhưng thiếu hai card overview và sort khác oracle; đây không còn là lỗi hết token. `ambiguous_004` là lỗi quyết định thật: “tỷ lệ offer” chưa nêu định nghĩa nhưng model tự chọn accepted_offer_rate và chạy query. Bốn ambiguous khác hỏi lại đúng loại/no-query, song câu chữ exact của oracle chưa khớp; `ambiguous_003` còn thiếu reason `missing_metric`. Bốn unsupported đã từ chối/no-query đúng reason, nhưng thiếu các cụm exact trong explanation nên grader vẫn FAIL. Prompt sau report này được bổ sung quy tắc hỏi rõ tử/mẫu số khi chỉ nói “tỷ lệ offer”; lịch sử hội thoại giới hạn 1.700 ký tự để giữ trần token, 33 test liên quan pass. Chưa gọi model lại sau chỉnh sửa cuối, không coi gate đã đạt.

## 30. Benchmark live đầy đủ — 17/10/2026

Lệnh `backend/scripts/test_local_postgres.py --live-benchmark` dựng cluster test riêng, khôi phục backup, xác minh fingerprint 16 bảng, chạy 2 migration test, 75 EXPLAIN plan (max planner cost 3905,65) và **149/149 integration** trước khi đo. Frontend được build production. `backend/.runtime/postgres-test-cgt3mf_w/benchmark-live.json` ghi 3 warm-up tách biệt và 30 mẫu request-to-render trong Chromium cho mỗi nhóm: saved dashboard **30/30 thành công, P95 333,294 ms** so với ngưỡng 3.000 ms; Agent live `gpt-6-luna` **30/30 thành công, P95 12.254,788 ms** so với ngưỡng 45.000 ms. P95 API/DB/render lần lượt: saved 106,865/59/137,898 ms; Agent 12.079,579/78/144,144 ms. Không có mẫu lỗi; `live_performance_gate_passed=true`. Đây là phép đo trên Windows, PostgreSQL 16.9, dataset import mẫu và frontend production local; không suy rộng thành SLA production.

33 lượt Agent gồm warm-up ghi 33 provider calls, tổng 95.040 input + 37.905 output tokens, estimated cost 0,030840 USD theo đơn giá đã cấu hình, không phải hóa đơn thực tế. Hard limit/budget vẫn được kiểm thử trong integration; benchmark không thay các bài kiểm thử đó. Sau đo, fingerprint cả 16 bảng source unchanged=true và cluster dừng. **P5-T03 đạt**, nhưng gate chất lượng 30 câu hỏi live và cấu hình runtime/reader trên DB chính vẫn chưa đạt.

## 31. Live evaluation lần năm và kiểm tra provisioning chỉ đọc — 18/10/2026

Lượt `--live-evaluate` trên cluster test `backend/.runtime/postgres-test-40u3ztey/` có 3 migration test, 149/149 integration, 75 EXPLAIN plan; 16 bảng source không đổi và cluster đã dừng. Kết quả raw **19 PASS/11 FAIL**: valid **18/20**, ambiguous **0/5**, unsupported **1/5**. `valid_001` và `valid_010` chỉ lệch sort mặc định so với oracle; các valid khác đạt. `ambiguous_004` không còn tự tính accepted_offer_rate: outcome nay là clarification, reason `ambiguous_rate_definition`, không sinh query. `ambiguous_003` nay có thêm `missing_metric`. Cả năm ambiguous đều hỏi lại/no-query và bốn unsupported đều từ chối/no-query đúng loại, song câu chữ diễn giải không khớp chuỗi exact của oracle nên raw grader vẫn FAIL. Không sửa oracle/grader để nâng điểm; gate formal vẫn false và cần review yêu cầu chấm text hoặc cách diễn đạt đầu ra trước nghiệm thu.

Sau security review, `provision_local_roles.py` đã hạn chế chuẩn hóa AST policy: chỉ bỏ cast `::text` vô hại trên cột/literal và qualifier `public.properties`, từ chối `char(1)` có thể mở rộng RLS cũng như schema khác. Thêm kiểm tra `SECURITY DEFINER`/view owner-executed trong các schema thuộc phạm vi. 26 unit test provisioning đạt; preflight **chỉ đọc trước khi áp dụng** trên DB chính trả `preflight_passed=true`, cluster `7520322186401912668`, migration head `0005_graph_checkpoints`, `changes_applied=false`.

Sau preflight, lệnh opt-in `--apply --confirm-database real_estate_db --confirm-cluster 7520322186401912668` đã **tạo bốn LOGIN ít quyền**: một runtime và ba reader ALL/CA/TX. Secret sinh ngẫu nhiên chỉ ghi vào `backend/.runtime/local-postgres-credentials.env` (Git ignore, Windows ACL owner-only trước khi ghi), không có URL/mật khẩu trong output. `--reconcile` đọc DB xác nhận đủ bốn role, membership, policy và quyền hiệu lực. Script `verify_main_roles.py` dùng chính credential mới, qua audit connection runtime/reader và chạy read-only trên DB chính: runtime principal hợp lệ; sales transaction count ALL/CA/TX lần lượt **1.000/135/132**, mọi reader scope hợp lệ. Bốn URL vẫn không ghi vào `backend/.env`; launch script nạp chúng từ artifact bảo vệ.

Tiếp theo `seed_local_demo.py` preflight chỉ đọc xác nhận chưa có bốn username demo, rồi `--apply` tạo bốn user với mật khẩu ngẫu nhiên lưu ở `backend/.runtime/local-demo-accounts.env` bị ignore và owner-only; không log secret. CLI `seed_dashboard.py --owner builder_all --confirm-demo --use-provisioned-roles` phát hành đúng một dashboard mẫu. `start-local.ps1 -UseProvisionedRoles` khởi động backend với URL từ artifact qua process environment và frontend ở `http://localhost:3000`, không ghi URL vào `.env`. Smoke qua frontend proxy: health OK, bốn login/me/logout OK, dashboard list/query OK; transaction count từ bản đã phát hành là Builder ALL 1.000, Viewer ALL 1.000, Viewer CA 135, Builder TX 132. Kết quả lấy bằng credential runtime/reader thật trên DB chính, không dùng superuser cho API. Server local đang chạy sau smoke; đây là bằng chứng sử dụng local, không phải nghiệm thu 30-case LLM.

Audit chỉ đọc lại sau khi seed/publication: đủ 16 bảng nguồn, row-count/baseline/FK diff so với import đều rỗng, không có duplicate PK hoặc FK orphan. Việc seed chỉ thêm metadata dưới `app`, không thay dữ liệu nguồn.

## 32. Audit DB chính và live evaluation lần sáu — 18/10/2026

Sau khi user thêm `AUDIT_DATABASE_URL`, audit **chỉ đọc** xác nhận lại `real_estate_db`: 16 bảng nguồn/2.203 dòng, 16 PK/16 FK/5 UNIQUE, không duplicate hoặc FK orphan; mọi chênh lệch schema, khóa, row count và baseline với import đều rỗng. Golden SQL độc lập đạt **15/15**. URL audit hiện dùng tài khoản `postgres` superuser/BYPASSRLS, chỉ dùng cho audit; runtime ứng dụng vẫn dùng role giới hạn quyền từ artifact riêng. Không ghi URL/mật khẩu vào báo cáo.

Sau sửa hẹp hướng dẫn donut không cần sort mặc định và bổ sung lý do từ chối hoa hồng theo đại lý, chạy `--live-evaluate` trên cluster tách biệt `backend/.runtime/postgres-test-akxpwvp0/`: **3 migration, 149/149 integration, 75 EXPLAIN** đạt; 16 bảng nguồn unchanged=true và cluster đã dừng. Lượt live này có **18 PASS/12 FAIL**, gồm valid **16/20**, ambiguous **0/5**, unsupported **2/5**; `release_gate_passed=false`. `valid_010` (donut) và `unsupported_002` (thiếu công thức hoa hồng, lịch sử môi giới–đại lý/fan-out) nay PASS. `valid_001` vẫn lệch ba sort; `valid_004` chọn line thay bar, `valid_007` thêm dimension year dù năm 2025 là filter, `valid_020` hỏi lại một tỷ lệ đã định nghĩa. Năm ambiguous vẫn đúng nhánh hỏi lại/no-query nhưng lệch câu exact; `unsupported_001/003/004` đúng nhánh từ chối/no-query nhưng thiếu hai cụm exact mỗi câu. Mọi query có evidence đều đúng kết quả SQL, không có query sai scope được ghi nhận. 29 provider calls, 81.869 input + 8.785 output tokens, chi phí ước tính 0,014636 USD; không phải hóa đơn thực tế.

Sau lượt chạy, prompt được làm rõ thêm về bar theo từng năm, phân biệt một năm cố định là filter, và tỷ lệ đề nghị *được chấp nhận* đã đủ định nghĩa. **Chưa có live evidence cho ba chỉnh sửa prompt này**; 19 targeted unit và toàn bộ **268 unit/static** sau chỉnh sửa đều pass. Bản đối chiếu từng FAIL theo ý nghĩa tại `docs/live-eval-review.md` chỉ là review sơ bộ, không đổi raw score. Mâu thuẫn riêng `valid_001`: oracle yêu cầu `sort=[]` cho chart tháng nhưng prompt/biểu đồ đường hiện cần thứ tự thời gian; chưa sửa oracle hoặc bỏ sort vì có thể làm biểu đồ sai thứ tự. Cần quyết định tiêu chí với user; formal gate và P3-T04/P4-T04/P5-T04 vẫn mở.

## 33. Live evaluation lần bảy sau chỉnh prompt — 18/10/2026

Lượt `--live-evaluate` trên cluster tách biệt `backend/.runtime/postgres-test-6tkjvh24/` đã qua 3 migration, 149/149 integration và 75 EXPLAIN; 16 bảng source unchanged=true, cluster dừng. Kết quả raw **21 PASS/9 FAIL**, gồm valid **19/20**, ambiguous **0/5**, unsupported **2/5**; `release_gate_passed=false`. `valid_004`, `valid_007` và `valid_020` đã PASS, xác nhận ba hướng dẫn prompt mới trong lượt này; `valid_010` và `unsupported_002` vẫn PASS. `valid_001` lần này không chỉ lệch ba sort: model còn bỏ ba KPI card mặc định, nên đây là lỗi plan thật ngoài mâu thuẫn sort. Năm ambiguous đều đúng outcome/reason/scope và không query nhưng khác câu `required_question`; `unsupported_001/003/004` đúng nhánh từ chối/reason/scope và không query nhưng thiếu các chuỗi `explanation_contains` exact. Mọi query có evidence đều khớp SQL độc lập. 29 provider calls, 84.856 input + 7.859 output tokens, chi phí ước tính 0,014548 USD; không phải hóa đơn thực tế. Chi tiết phân biệt lỗi thực và lỗi câu chữ trong `docs/live-eval-review.md`; chưa sửa oracle/grader hoặc tick gate.

## 34. Kiểm chứng local sau live evaluation — 18/10/2026

Hai cổng local 3000/8000 còn lắng nghe; frontend proxy `/api/health` trả HTTP 200. `backend/scripts/verify_local_app.py` chạy lại thành công qua proxy: bốn tài khoản demo login/session và published KPI đúng scope, transaction count Builder ALL 1.000, Viewer ALL 1.000, Viewer CA 135, Builder TX 132. Audit DB chính trong transaction chỉ đọc vẫn có 16 bảng, không có chênh lệch column/PK/UNIQUE/FK/row count/baseline với import hoặc FK orphan; golden SQL **15/15**. Đây là smoke hiện tại của local app và DB, không thay thế gate chất lượng LLM 30 case.

Runbook được rà lại theo setup sạch: các lệnh seed/verify nay gọi trực tiếp `.venv/Scripts/python.exe` thay vì phụ thuộc `python` của hệ thống; ghi rõ preflight trước apply, phát hành dashboard mẫu và smoke qua proxy. Đây là sửa tài liệu, không chạy lại seed trên DB chính vốn đã có bốn demo user và một dashboard mẫu.

## 35. Live evaluation lần tám và quyết định review ngữ nghĩa — 19/10/2026

User chọn **nghiệm thu câu hỏi lại/từ chối theo ý nghĩa, có review lưu bằng chứng**, thay vì bắt buộc nguyên văn. Quyết định này không đổi `status` raw, oracle hoặc script chấm. Lượt `--live-evaluate` trên cluster test `backend/.runtime/postgres-test-fb8fpc4a/` đã qua 3 migration, 149/149 integration, 75 EXPLAIN; 16 bảng source unchanged=true, cluster dừng. Raw **19 PASS/11 FAIL**: valid **17/20**, ambiguous **0/5**, unsupported **2/5**, formal gate false. 29 provider calls, 83.551 input + 7.690 output tokens, chi phí ước tính 0,014302 USD.

Ba valid FAIL: `valid_001` trả lỗi tạo plan, `valid_012` chọn bar thay table, `valid_019` chọn table thay bar. `valid_001` không có mã lỗi chi tiết trong artifact cũ; evaluator nay ghi `actual_error_code` an toàn ở các lượt sau. Review 8 text FAIL thấy `ambiguous_001/002/003/005` và `unsupported_001/003/004` đạt ý nghĩa, đúng reason/scope và không query; riêng `ambiguous_004` còn thêm `ambiguous_time_range` và hỏi làm rõ “năm ngoái”, trái quy ước current_date, nên **không được duyệt ngữ nghĩa**. Vì valid chỉ 17/20 và ambiguous còn một lỗi thật, cả gate ngữ nghĩa lẫn formal đều **chưa đạt**. Chi tiết review ở `docs/live-eval-review.md`.

Sau lượt tám, prompt được bổ sung quy tắc “bảng” → table, so sánh theo nhóm → bar, và “năm ngoái” = năm dương lịch trước theo Asia/Ho_Chi_Minh. Hướng dẫn tổng quan sales được rút gọn để giữ giới hạn token; **270 unit/static pass**, bao gồm kiểm tra ngân sách bảo thủ. Các chỉnh sửa sau lượt tám chưa có live evidence, không đánh dấu P3-T04/P4-T04/P5-T04 hoàn tất.

## 36. Live evaluation lần mười và review theo ý nghĩa — 19/10/2026

Sau lượt chín 21/30 raw (valid 19/20), review phát hiện `ambiguous_002` gắn `ambiguous_metric` thay vì `missing_metric` cho yêu cầu so sánh hoàn toàn không nêu KPI. Bổ sung normalizer hẹp chỉ cho so sánh/xếp hạng mới không có đại lượng; không đổi yêu cầu đã nêu KPI hay hội thoại có ngữ cảnh. 271 unit/static pass sau thay đổi.

Lượt `--live-evaluate` thứ mười trên cluster `backend/.runtime/postgres-test-k79g049o/` qua 3 migration, **149/149 integration**, 75 EXPLAIN; 16 bảng nguồn unchanged=true, cluster đã dừng. Raw **21 PASS/9 FAIL**: valid **19/20**, ambiguous **0/5**, unsupported **2/5**; `release_gate_passed=false`. `valid_001` lần này đủ ba card/ba chart và KPI, chỉ lệch ba sort; mọi query thực thi trong bộ đều đúng SQL đối chiếu độc lập. 29 provider calls, 84.363 input + 8.563 output tokens, chi phí ước tính 0,014841 USD.

Theo phương pháp user đã duyệt, review từng case ở `docs/live-eval-review.md` (artifact SHA-256 `cdc27a3fa8a9f3e481e0ab8f4f8f85ae6b31cebe849cd90b99bb73ab7570d6f1`) xác nhận **10/10 ambiguous/unsupported xử lý đúng ý nghĩa**, không draft/query, đúng reason/quyền/scope; tám raw FAIL trong nhóm này chỉ lệch câu chữ. Valid raw 19/20 vượt ngưỡng 18/20, nên **gate ngữ nghĩa 30-case đạt**. Đây là adjudication bổ sung, **không chuyển raw 21/30 thành 30/30**, không sửa oracle/grader và không khẳng định strict formal gate đạt. P3-T04/P4-T04 đã đóng theo tiêu chí nghiệm thu nghiệp vụ đã duyệt; P5-T04 vẫn mở do demo cuối trên app/DB chính và quyết định sort `valid_001` chưa hoàn tất.

## 37. Setup sạch và E2E khi app local đang chạy — 19/10/2026

Lệnh trực tiếp `backend/scripts/test_local_postgres.py --e2e` trên workspace đạt restore, 3 migration, 75 EXPLAIN và **149/149 integration**, nhưng frontend test thoát trước Playwright vì Next.js 16 từ chối `next dev` thứ hai trong cùng `frontend/.next/dev` khi app local cổng 3000 đang chạy. Log test app xác nhận xung đột lock; 16 bảng nguồn test unchanged=true, cluster dừng. Không kill/restart app của user; đây không phải lỗi UI/API nhưng cũng **không** phải E2E pass cho lượt trực tiếp.

Thay bằng `backend/scripts/verify_clean_setup.py --e2e` trong snapshot riêng `backend/.runtime/clean-setup-226eszrh/`: venv mới, cài Python từ `requirements.lock`, `pip check`, `npm ci`, API generation, unit/static, lint, production build, typecheck, browser install và PostgreSQL/browser gate đều exit 0. Trong snapshot, **149/149 integration và 16/16 Playwright pass** (gồm năm edit, approval, Viewer scope, restart, lỗi provider/reader và phục hồi), 16 bảng source unchanged=true, cluster dừng. `setup-report.json` và log từng bước trong artifact bị ignore. Sau đó frontend proxy app local ban đầu vẫn `/api/health` HTTP 200. Runbook đã ghi rõ giới hạn lock Next và lệnh snapshot; bài này không thay live-provider demo trên DB chính.

## 38. Demo cuối trên DB chính — 19/10/2026

Thêm `backend/scripts/verify_main_demo.py`, dùng credential runtime/reader và bốn demo account đã cấp; không reseed mật khẩu hoặc migration. Frontend chạy từ snapshot source mới, backend chạy mã workspace. Vì recovery MVP dùng một process epoch/database, backend local cổng 8000 được dừng có xác minh PID trước lượt nghiệm thu; runner từ chối nếu cổng này còn hoạt động. Chỉ các process của lượt demo được restart/stop. Sau nghiệm thu, backend local được khôi phục bằng credential giới hạn quyền và frontend cũ tiếp tục phục vụ `http://localhost:3000`.

Lượt đạt: `backend/.runtime/main-live-demo-ui3as7_6/acceptance.json`, SHA-256 **`e383360e513387f1d7315ef826027e1fd024fd473b4478bbd1b08670d2bda26e`**. Browser evidence `frontend/test-results-live/smoke-Live-planner-five-edits-approval-and-Viewer-scope/live-run-evidence.json` bên trong artifact có SHA-256 **`149e673286ec89c285efdcf3d9c4a4f9e48b838503e6d23643230db2d8247a79`**. Playwright **1/1 pass**, toàn suite 51,6 giây, test 49,1 giây; `complete=true`.

| Bước mục 6.1 kế hoạch | Bằng chứng thực thi |
|---|---|
| 1–2. Builder ALL tạo dashboard 2025 theo tháng/bang/loại | Đúng câu `valid_001`, đủ ba card + ba chart, KPI/SQL/kỳ 2025, mode live |
| 3. Năm edit | Lọc CA → horizontal bar loại BĐS → thêm sold_property_count → bỏ chart bang → năm 2024; ID/widget không liên quan giữ nguyên, presentation-only tái dùng query |
| 4. Restart chờ duyệt | PID backend đổi thực sự; hội thoại, draft/hash và kết quả trước/sau giống nhau |
| 5. Approve rồi retry | Cùng dashboard/version 1; đối chiếu DB không có version thừa |
| 6. Viewer CA và filter | CA có dữ liệu; TX no_data; control dashboard không lưu CA cho ALL+TX có dữ liệu và CA+TX rỗng |
| 7. Chặn quyền | Viewer create/message/approval/read draft trả 403; Builder TX đọc draft người khác trả 404 |
| 8. Sửa bản đã duyệt | Thêm lượt live chuyển sang 2025; Viewer giữ nguyên detail version 1 trong lúc draft; sau approve mới thấy đúng spec version 2 |

Bảy lượt đều dùng provider thật `gpt-6-luna`: **7 calls, 25.891 input + 1.775 output token**, chi phí ước tính **0,004127 USD**. Toàn bộ **39 đối chiếu query** qua bảy preview khớp SQL tham chiếu độc lập (bao gồm query tái dùng); không đồng nhất con số đối chiếu này với số lần SQL thực thi vật lý. DB chỉ có versions **[1, 2]** cho dashboard `94b5521d-06de-4d70-831e-3d2741b7c218`. Fingerprint read-only của 16 bảng nguồn trước/sau giữ nguyên. Đã kiểm ảnh preview và trạng thái empty Viewer. Sort tháng tăng dần, chart bang/loại theo giá trị giảm dần; `valid_001` vẫn FAIL raw exact về sort, không sửa oracle. Demo cụ thể nay đạt; valid raw 19/20 vẫn vượt ngưỡng 18/20.

Hai lượt trước được giữ nguyên bằng chứng: `main-live-demo-hts9ogv1` lỗi `invalid_output` ngay lượt tạo, 1 call/2.909 input/720 output/ước tính 0,000724 USD, không có dashboard phát hành; báo cáo đầu thiếu usage của lượt thất bại, số này được đối chiếu lại ledger. Lượt đầu còn chạy cạnh backend cũ nên không dùng làm bằng chứng đúng cấu hình singleton. `main-live-demo-myuloed1` hoàn thành bảy lượt và 25 đối chiếu query nhưng assertion cuối sai vì detail API loại optional null còn draft giữ null; bản đã duyệt thực tế đã đúng version 2. Đã sửa phép so sánh theo `response_model_exclude_none` và chạy lại toàn hành trình để có lượt PASS ở trên. Hai dashboard từ lượt hai và ba được giữ trên DB làm demo; draft lỗi cũng được giữ để truy vết, không xóa dữ liệu người dùng.

Kiểm tra hậu phục hồi: `verify_local_app.py` exit 0, health proxy OK và bốn login/me/query/logout đạt; counts ALL 1.000, CA 135, TX 132. `verify_main_roles.py` xác nhận runtime principal và reader scope đều hợp lệ. Smoke local nay chọn dashboard CLI mẫu thay vì yêu cầu toàn workspace chỉ có một dashboard. `.env` vẫn `LLM_MODE=mock`; live chỉ bật trong process nghiệm thu. Muốn dùng AI thật khi thao tác thường ngày, đổi mode thành live và khởi động lại backend theo README.

## 39. Đối chiếu hoàn tất và giới hạn bàn giao

Đã kiểm lại **103 artifact được nêu trong các dòng Tạo/Sửa của kế hoạch**, không thiếu file/thư mục; 24/24 task có bằng chứng dưới đây. Kết quả hiện tại: **292 unit/static pass**, targeted lint và typecheck đạt; snapshot sạch mục 37 có **290 unit/static**, build/lint/typecheck, 149 integration và 16 Playwright. Hai test mới xác nhận dùng account DB chính không reseed và không đi vào suite fault/seeding. Mã ứng dụng/provider không đổi sau lượt live evaluation thứ mười; thay đổi cuối là runner/demo test, smoke chọn dashboard mẫu và tài liệu.

| Phạm vi task/gate | Bằng chứng đã đối chiếu |
|---|---|
| P0-T01–03: dữ liệu, KPI, oracle | Audit DB chính mục 28–29; 16 bảng/2.203 dòng, golden 15/15; corpus 20 valid + 5 ambiguous + 5 unsupported |
| P1-T01–04: scaffold/migration/auth/RLS | Setup sạch mục 37; rehearsal báo source_fingerprint_equal/restore_comparison_empty/source_rows_unchanged_by_migration=true, golden 15/15, head 0005; DB chính RLS bốn bảng và runtime/readers kiểm lại mục 38 |
| P2-T01–05: contracts/compiler/executor/dashboard/UI | Unit/integration, 75 EXPLAIN, golden/scope; 16 browser case gồm chart/table, precision, HTML-as-text, resize, filter/loading/error và Viewer; build/generation ở setup sạch |
| P3-T01–04: adapter/budget/graph/30-case | Unit/integration cho output/schema, timeout/retry, hard budget/concurrency; run10 valid 19/20 + other 10/10 review, mọi executed query đúng; raw 21/30 giữ nguyên |
| P4-T01–04: edits/checkpoint/HITL/full KPI | 149 integration, 16 browser regression và main live demo: năm edit, restart, ownership, stale/retry/crash recovery, version isolation; bảy KPI/golden và catalog compatibility |
| P5-T01–02: bảo mật/hồi phục | Security matrix, RLS bằng login thật, prompt injection/PII/SQL/CSRF/ownership; browser reader/provider fault + restart, main DB source fingerprints unchanged |
| P5-T03: hiệu năng | Artifact `postgres-test-cgt3mf_w/benchmark-live.json` được mở kiểm lại: gate true, 30 mẫu mỗi nhóm + 3 warm-up, saved P95 333,294 ms và AI P95 12.254,788 ms, không lỗi |
| P5-T04: setup/restore/runbook/demo | Setup sạch 11 bước exit 0; main backup restore fingerprint/golden/migration đạt; đủ tám bước demo mục 38; local app đã phục hồi và login/scope đạt |

Secret scan kiểm 162 file Git-visible không thấy mẫu API key/credential URL thật; đối chiếu riêng **10 secret local** với source Git-visible và toàn `git log --all -p` không có giá trị trùng. Env thật, credential artifact, runtime, ảnh và backup được ignore. Đây là kiểm tra phạm vi cụ thể, không phải cam kết phát hiện mọi loại secret. Worktree vẫn chưa commit/push các thay đổi mới.

Giới hạn bàn giao: exact-match grader vẫn false theo quyết định review ngữ nghĩa đã duyệt; provider có thể trả output không hợp lệ như lượt đầu và hệ thống trả lỗi, không phát hành. Đã ghi 5 high dependency dev-only ở chuỗi lint và production audit 0 tại mục 24/runbook; không tự force-upgrade dependency. Chạy một backend process, dữ liệu synthetic, tiền giữ đơn vị dataset; chưa cloud/multi-tenant/agent nhiều tầng hoặc các KPI ngoài phạm vi. Những giới hạn này được công bố, không quy thành feature đã triển khai.
