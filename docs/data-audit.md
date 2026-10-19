# Khảo sát dữ liệu — P0-T01

## Trạng thái và phạm vi bằng chứng

Thời điểm chạy lại: **2026-10-12 10:21:39 UTC** (17:21:39 Asia/Ho_Chi_Minh). Môi trường: Windows, Python 3.11, workspace `D:/AI-Dashboard-Agent`.

| Nguồn bằng chứng | Trạng thái | Kết luận được phép |
|---|---|---|
| File import `data/real-estate-sales-sample-database-v3-sql-postgres.sql` | Đã khảo sát đầy đủ, chỉ đọc | Các số liệu trong mục “Kết quả file import” |
| PostgreSQL đang chạy | **Đã audit chỉ đọc ngày 17/10/2026** | PostgreSQL 16.9, database `real_estate_db`, đủ 16 bảng; các chênh lệch schema, khóa, số dòng và baseline đều rỗng |

Lịch sử trước 17/10: PostgreSQL tại `localhost:5432` trả trạng thái `accepting connections` nhưng chưa có credential audit. Đã giải quyết bằng URL audit trong `.env` ignored; báo cáo không ghi DSN/secret.

P0-T01 đạt gate khảo sát DB thật bằng `BEGIN READ ONLY`. Credential hiện là `postgres` superuser; audit xác minh dữ liệu và cấu trúc nhưng không chứng minh RLS của reader hay cấp quyền runtime. Chưa migration/ghi vào DB chính.

## Cách tái lập

Khảo sát file import, không cần package ngoài:

```powershell
python backend/scripts/audit_db.py
```

Khảo sát đồng thời DB thật (cần `psycopg` 3). Điền `AUDIT_DATABASE_URL=postgresql://postgres:<mật-khẩu>@localhost:5432/real_estate_db` trong `backend/.env`, rồi chạy:

```powershell
.venv\Scripts\python.exe backend/scripts/audit_db.py --require-db
```

Script mở kết nối live bằng `BEGIN READ ONLY`, bắt buộc `transaction_read_only=on`, đặt timeout kết nối 5 giây, chỉ chạy `SELECT`/catalog queries, rollback rồi đóng kết nối. Output không chứa DSN, giá trị dữ liệu hay tên role/owner nguyên văn (chỉ fingerprint 12 ký tự). Audit live kiểm kê direct/effective table privileges, quyền schema, owner fingerprint, RLS, PK/FK/UNIQUE/index và so sánh constraint/schema/count/baseline với import. `--require-db` trả exit code 2 khi thiếu DSN hoặc audit live lỗi.

## Kết quả file import

### Định danh nguồn

- Kích thước: 140.909 byte.
- SHA-256: `c585831fc15942bfa3f3fa793d4d4ebedfde81905ae1214781431001043abbe3`.
- Header tự mô tả dữ liệu synthetic, deterministic, CC0-1.0, seed `flagship:real-estate-sales-sample-database:1k`.
- Schema không được ghi tường minh; khi chạy với `search_path` mặc định, 16 bảng được tạo trong `public`.
- File bọc thao tác trong `BEGIN`/`COMMIT`. Audit chỉ phân tích file, không thực thi import.

### Schema, khóa và index

- 16 bảng; mỗi bảng có đúng một primary key một cột: 16 PK.
- 16 foreign key; đầy đủ trong [data-dictionary.md](data-dictionary.md).
- 5 ràng buộc `UNIQUE`: `agents.license_number`, `appraisers.license_number`, `inspectors.license_number`, `payment_methods.method_name`, `property_types.type_name`.
- Không có câu lệnh `CREATE INDEX` tường minh. Nếu import được chạy trên PostgreSQL, 16 PK và 5 `UNIQUE` sẽ tạo 21 index hỗ trợ constraint; đây là hành vi dự kiến từ DDL, **không phải inventory index của DB thật**.
- File không có `GRANT`/`REVOKE`; privilege và thuộc tính role thực tế chưa thể xác minh.

### Số dòng, NULL và khoảng ngày

| Bảng | Dòng | NULL | Khoảng ngày/timestamp trong file |
|---|---:|---:|---|
| `agencies` | 16 | 0 | — |
| `agents` | 125 | 0 | — |
| `agent_agencies` | 125 | 70 | `start_date`: 2021-01-05 → 2025-12-25; `end_date`: 2022-07-13 → 2029-04-03 |
| `appraisers` | 16 | 0 | — |
| `clients` | 125 | 0 | `created_at`: 2023-01-04 16:15:40 → 2025-12-01 15:12:31 |
| `inspectors` | 16 | 0 | — |
| `neighbourhoods` | 16 | 0 | — |
| `payment_methods` | 6 | 0 | — |
| `property_types` | 8 | 0 | — |
| `properties` | 125 | 0 | `created_at`: 2021-01-28 14:10:29 → 2025-12-14 11:44:29 |
| `appraisals` | 125 | 0 | `appraisal_date`: 2021-02-20 → 2025-12-14 |
| `inspections` | 125 | 0 | `inspection_date`: 2021-02-08 → 2025-12-07 |
| `offers` | 125 | 0 | `offer_date`: 2021-02-23 → 2026-03-25 |
| `property_neighbourhoods` | 125 | 0 | `assigned_date`: 2023-01-13 → 2025-12-12 |
| `repairs` | 125 | 0 | `repair_date`: 2021-02-24 → 2026-08-04 |
| `transactions` | 1.000 | 0 | `transaction_date`: 2021-02-12 → 2026-07-13 |

70 giá trị NULL đều nằm ở `agent_agencies.end_date`, cột duy nhất được khai báo nullable. Các cột còn lại không có NULL trong import.

### Duplicate và FK orphan

- Duplicate primary key: 0 trên cả 16 bảng.
- Duplicate trên 5 khóa `UNIQUE`: 0.
- FK orphan: 0 trên cả 16 quan hệ FK.
- Audit đếm duplicate/orphan trên các tuple đã parse; không xuất giá trị nhận dạng hoặc PII.

### Baseline giao dịch từ file

Các tổng sau dùng `transactions` làm grain, nối đúng một lần đến `properties` qua `property_id` để lọc `state`; tiền giữ bằng `Decimal`, không làm tròn trung gian.

| Scope | Giao dịch | Bất động sản khác nhau | Tổng `sale_price` |
|---|---:|---:|---:|
| ALL | 1.000 | 125 | 870.798.679,64 |
| CA | 135 | 22 | 107.064.313,73 |
| TX | 132 | 14 | 128.635.636,80 |

Các số này khớp baseline import trong kế hoạch. Đơn vị tiền tệ chưa được dataset xác nhận; không gán nhãn USD.

## Quan hệ nhiều-nhiều và fan-out

### Lịch sử môi giới–đại lý

`agent_agencies` có 125 dòng nhưng chỉ 64 `agent_id` khác nhau. Với điều kiện ngày:

```sql
aa.agent_id = t.agent_id
AND aa.start_date <= t.transaction_date
AND (aa.end_date IS NULL OR t.transaction_date <= aa.end_date)
```

- 631/1.000 giao dịch không khớp đại lý nào.
- 200/1.000 giao dịch khớp nhiều hơn một đại lý.
- Chỉ 169 giao dịch khớp đúng một đại lý.
- Một giao dịch có thể khớp tối đa 9 dòng lịch sử trong file.

Vì vậy không dùng join này để tính KPI cộng gộp hoặc phân tích theo đại lý ở MVP.

### Bất động sản–khu dân cư

`property_neighbourhoods` có 125 dòng cho 70 bất động sản khác nhau. Có 30 bất động sản mang nhiều hơn một liên kết, tạo thêm 55 dòng so với grain một dòng/bất động sản; tối đa 10 liên kết cho một bất động sản. Join trực tiếp với transaction có thể nhân bản fact. MVP dùng `properties.state` và `properties.city`, không dùng neighbourhood làm dimension KPI.

## So sánh DB thật với file import

Chưa có dữ kiện DB thật để so sánh. Không có chênh lệch nào được tuyên bố là “0”. Khi có credential, output live của script sẽ ghi riêng:

- PostgreSQL version, database, fingerprint của current user, `rolsuper`/`rolbypassrls`, và xác nhận transaction read-only;
- columns, PK, FK, index và privilege hiện hành;
- row count, NULL, khoảng ngày, duplicate PK/unique và FK orphan;
- baseline ALL/CA/TX, hai phép kiểm tra fan-out;
- `row_count_differences` và `baseline_differences` so với chính file import có hash ở trên.

Nếu DB khác import, baseline DB phải được ghi nhận riêng; không sửa dữ liệu để ép khớp.

## Baseline KPI P0-T02 từ file import

Bảy KPI đã được định nghĩa tại [kpi-catalog.md](kpi-catalog.md). Đây vẫn là **baseline file import**, không được gọi là kết quả DB thật. Ba fact được tính bằng ba phép tổng hợp độc lập; không query nào join `transactions`, `offers` và `repairs` với nhau.

| Scope | `sales_value` | `transaction_count` | `average_sale_value` | `sold_property_count` | `offer_count` | `accepted_offer_rate` | `repair_cost` |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 870.798.679,64 | 1.000 | 870.798,67964 | 125 | 125 | 35/125 = 0,28 | 2.993.224,70 |
| CA | 107.064.313,73 | 135 | 107.064.313,73 / 135 | 22 | 14 | 5/14 | 385.283,39 |
| TX | 128.635.636,80 | 132 | 974.512,40 | 14 | 11 | 2/11 | 456.693,59 |

Phân số được giữ ở bảng khi biểu diễn thập phân là vô hạn. Giá trị Decimal đầy đủ, tử số/mẫu số, kỳ 2025, kỳ rỗng và từng ngày biên nằm trong `backend/tests/fixtures/golden_results.json`. Các kiểm tra biên xác nhận ngày nhỏ nhất/lớn nhất của từng fact đều được lấy khi dùng khoảng ngày nửa mở.

Lần validation P0-T02 ngày 12/10/2026 chạy `python -m pytest -q` trong `backend/`: 5 test pass. Test tính lại oracle trực tiếp từ tuple của file import, kiểm tra đủ ALL/CA/TX cho toàn thời gian, năm 2025 và kỳ rỗng 2030; kiểm tra sáu ngày biên, Decimal đến `0.01`, fraction trung bình/tỷ lệ, và kiểm tra tĩnh mỗi prepared SQL chỉ tham chiếu một fact. Đây không phải bằng chứng thực thi SQL trên DB live.

`sold_property_count` khác rõ `transaction_count` (ALL: 125 so với 1.000), chứng minh phải dùng `COUNT(DISTINCT property_id)`. Mẫu số tỷ lệ accepted là toàn bộ offer trong tập lọc: ALL 125, CA 14, TX 11; không chỉ accepted + rejected. Khi kỳ rỗng, count là 0 còn sum/trung bình/tỷ lệ là `null` và trạng thái `no_data`.

## Hạn chế và quyết định cho task sau

- Catalog P0-T02 đã được kiểm chứng theo file import; audit DB thật ngày 17/10 xác nhận baseline và golden SQL độc lập đạt 15/15 case trên DB chính.
- Các trường `email`, `phone`, tên người và địa chỉ là PII/nhạy cảm; lớp analytics sau này không được cấp các cột không cần thiết.
- Các timestamp trong file không kèm múi giờ. Không suy diễn timezone.
- Audit DB chính ngày 17/10 đã kiểm kê owner fingerprint, quyền, RLS và index vật lý; tài khoản audit là superuser/BYPASSRLS nên không dùng kết quả này làm bằng chứng quyền reader.

## Audit database chính ngày 17/10/2026

Chạy `.venv\Scripts\python.exe backend/scripts/audit_db.py --require-db --output backend/.runtime/main-db-audit.json` trên Windows bằng credential trong `.env` ignored. Artifact JSON ghi timestamp `2026-10-16T21:08:34Z` (04:08:34 ngày 17/10 Asia/Ho_Chi_Minh). Kết nối xác nhận `transaction_read_only=on`; script chỉ chạy SELECT/catalog, rollback và đóng kết nối. Không ghi DSN hoặc dữ liệu PII vào report.

DB xác nhận PostgreSQL **16.9**, tên `real_estate_db`, 16 bảng nguồn tổng 2.203 dòng, 16 PK, 16 FK, 5 UNIQUE. Không có duplicate PK/UNIQUE hoặc FK orphan. So với file import: `missing_tables`, `column_differences`, `primary_key_differences`, `unique_constraint_differences`, `foreign_key_differences`, `row_count_differences`, `baseline_differences` đều rỗng. NULL, khoảng ngày, index, quyền và security của từng bảng nằm trong artifact. Các baseline thật: ALL 1.000 giao dịch/870.798.679,64/125 bất động sản; CA 135/107.064.313,73/22; TX 132/128.635.636,80/14. Phân tích fan-out đại lý/lịch sử và property–neighbourhood cũng khớp dữ liệu import.

Role audit là superuser/BYPASSRLS và có quyền schema `CREATE`; bốn bảng fact/dimension `properties`, `transactions`, `offers`, `repairs` chưa bật RLS trên DB chính. Đây là trạng thái trước migration, không phải kiểm chứng reader ALL/CA/TX. Các việc tạo role, backup và migration phải theo runbook, thử trên cluster khôi phục riêng trước khi tác động DB chính.

`backend/scripts/verify_golden_db.py` dùng chính `backend/tests/fixtures/golden_queries.sql` (ba prepared statement độc lập cho sales/offers/repairs), so sánh `backend/tests/fixtures/golden_results.json` bằng Decimal và kiểm tra trạng thái `no_data`. Chạy trên DB chính bằng `AUDIT_DATABASE_URL`, `BEGIN READ ONLY`, `statement_timeout=5s`, rollback: **15/15 case đạt**, gồm ALL/CA/TX, năm 2025, kỳ rỗng và sáu ngày biên. Output chỉ chứa case ID và tên field khác biệt khi fail; không chứa DSN hoặc dữ liệu hàng.

## Trạng thái triển khai bảo mật analytics P1-T03 (lịch sử trước 15/10/2026)

Migration `0002_analytics_security` đã được chuẩn bị với RLS cho `properties`, `transactions`, `offers`, `repairs`, column-level grants không gồm PII, và ba view `security_invoker`. Migration có guard từ chối ghi đè cấu hình RLS/tên reader role, từ chối `PUBLIC` grant/CREATE không an toàn và consumer cũ chưa review trước khi bật default-deny; migration không chứa DML nguồn. Reader pool kiểm principal và effective privileges khi kết nối. Đây chưa phải bằng chứng DB thật: chưa có ba reader credential trên DB test đã restore, nên totals CA/TX, quyền trực tiếp/qua view và chống đổi role vẫn `BLOCKED`.

### Cập nhật 15/10/2026 — PostgreSQL test đã restore

`backend/scripts/test_local_postgres.py` đã chạy thành công với PostgreSQL **16.9**, cluster hoàn toàn riêng ở cổng trống. Dataset synthetic được import vào `dashboard_source`, backup custom-format rồi restore sang `dashboard_test`; role migration khác runtime và ba reader login riêng. 2 test migration và 36 test auth/RLS/executor/dashboard API pass, không skip trong lần chạy này. Audit DB chính vẫn chưa chạy; baseline ở trên tiếp tục là baseline import và bản restore test.

Thực thi live đã phát hiện và sửa lỗi `aclexplode` trên ACL cột rỗng trong migration: bỏ việc ép NULL thành mảng rỗng không có dimension, chỉ explode ACL có `cardinality > 0`. Sau sửa, chu kỳ upgrade/downgrade/upgrade giữ nguyên số dòng của đủ 16 bảng nguồn và các probe quyền thực trả SQLSTATE `42501`.

Executor đối chiếu 11 golden sales case (ALL/CA/TX, năm 2025, kỳ rỗng và hai ngày biên), chạy đủ tám dimension không fan-out, alternating scope và scope/filter giao rỗng. Timeout thật được tạo bằng table lock trong DB test; executor rollback và pool truy vấn lại thành công sau khi thả lock. Cùng connection xác nhận `transaction_read_only=on`, `statement_timeout=5s` trong transaction và reset sau khi trả pool.

27 EXPLAIN plan đại diện (tổng + tám dimension, trên ba reader) có root Total Cost tối đa **3905.65**, artifact `backend/.runtime/postgres-test-ab9wb96c/explain-costs.json`. Ngưỡng mặc định chọn **10000** để có khoảng trống cho các query mẫu; cần hiệu chỉnh lại theo statistics và dữ liệu DB chính. Đây là planner units, không phải số đo latency hay tiền.

