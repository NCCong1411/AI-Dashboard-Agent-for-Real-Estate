# KPI catalog — MVP bất động sản

Catalog ID: `real-estate-mvp`  
Runtime catalog version: `1.1.0-offers-repairs`
Evidence: file import SHA-256 `c585831fc15942bfa3f3fa793d4d4ebedfde81905ae1214781431001043abbe3`

Đây là catalog nghiệp vụ đã kiểm chứng trên file import; audit chỉ đọc DB `real_estate_db` ngày 17/10/2026 xác nhận đủ 16 bảng, row counts và baseline ALL/CA/TX khớp, không có chênh lệch schema/khóa. Golden SQL độc lập đã đạt 15/15 trường hợp trên DB chính bằng giao dịch chỉ đọc; không âm thầm thay nghĩa KPI.

Runtime 1.1.0 bật đủ bảy KPI dưới đây. Phiên bản `1.0.0-import-baseline` vẫn đọc/chạy được với đúng bốn KPI sales ban đầu; không cho dùng KPI mới dưới version cũ. Đọc, lọc hoặc đổi tên dashboard cũ không tự đổi catalog. Khi người dùng yêu cầu thêm KPI offers/repairs, backend nâng **draft** lên 1.1.0 và cập nhật version của từng query; bản đã duyệt cũ giữ nguyên đến khi duyệt draft mới. Bốn công thức sales, ngày và grain không đổi.

## Quy tắc chung

- Mỗi query chỉ dùng **một fact**: `transactions`, `offers` hoặc `repairs`. Chỉ join dimension `properties`/`property_types` cần thiết; không join nhiều fact rồi `SUM`.
- Scope `ALL`, `CA`, `TX` được áp qua `properties.state`. Scope quyền và filter người dùng phải giao nhau; filter không được mở rộng quyền.
- Khoảng ngày là nửa mở: `date >= start_date AND date < end_date_exclusive`. Cách này giữ đúng cả ngày biên và tránh chồng kỳ.
- Không nêu thời gian nghĩa là toàn bộ khoảng dữ liệu hiện có; UI phải ghi rõ. Ngày tương đối sau này tính theo `Asia/Ho_Chi_Minh` tại lúc request.
- Tiền dùng PostgreSQL `numeric`/Python `Decimal`; không dùng float và không làm tròn khi tính. Chỉ format/làm tròn khi hiển thị.
- Đơn vị của `sale_price`, `offer_amount` và `cost` là **đơn vị tiền của dataset**; chưa có bằng chứng để gọi là USD.
- Query không có dòng fact trả trạng thái `no_data`: các KPI `SUM`/trung bình/tỷ lệ là `null`; KPI đếm là `0`. Không biến `SUM` rỗng thành 0 và không chia cho 0.
- `accepted` so khớp chính xác với giá trị `offers.status = 'accepted'` trong dataset.

## Danh mục bảy KPI

| KPI ID | Tên hiển thị | Fact / grain | Trường ngày | Công thức chuẩn | Đơn vị | Dimension hợp lệ | Phase |
|---|---|---|---|---|---|---|---|
| `sales_value` | Giá trị giao dịch | `transactions`, một giao dịch | `transaction_date` | `SUM(transactions.sale_price)` | Đơn vị tiền dataset | thời gian, bang, thành phố, loại BĐS, môi giới | 2 |
| `transaction_count` | Số giao dịch | `transactions`, một giao dịch | `transaction_date` | `COUNT(transactions.transaction_id)` | giao dịch | thời gian, bang, thành phố, loại BĐS, môi giới | 2 |
| `average_sale_value` | Giá trị giao dịch trung bình | `transactions`, một giao dịch | `transaction_date` | `SUM(sale_price) / NULLIF(COUNT(transaction_id), 0)` | Đơn vị tiền dataset / giao dịch | thời gian, bang, thành phố, loại BĐS, môi giới | 2 |
| `sold_property_count` | Số BĐS có giao dịch | tập BĐS trong các giao dịch đã lọc | `transaction_date` | `COUNT(DISTINCT transactions.property_id)` | bất động sản | thời gian, bang, thành phố, loại BĐS, môi giới | 2 |
| `offer_count` | Số đề nghị | `offers`, một đề nghị | `offer_date` | `COUNT(offers.offer_id)` | đề nghị | thời gian, bang, thành phố, loại BĐS | 4 |
| `accepted_offer_rate` | Tỷ lệ đề nghị được chấp nhận | `offers`, một đề nghị | `offer_date` | `COUNT(offer_id) FILTER (WHERE status='accepted')::numeric / NULLIF(COUNT(offer_id), 0)` | tỷ lệ 0–1 | thời gian, bang, thành phố, loại BĐS | 4 |
| `repair_cost` | Chi phí sửa chữa | `repairs`, một lần sửa chữa | `repair_date` | `SUM(repairs.cost)` | Đơn vị tiền dataset | thời gian, bang, thành phố, loại BĐS | 4 |

“Thời gian” hỗ trợ grain ngày, tháng, quý và năm trên đúng trường ngày của fact. `sold_property_count` áp `DISTINCT` sau toàn bộ filter; nó không đồng nghĩa `transaction_count` khi một BĐS có nhiều giao dịch.

Offers/repairs không có quan hệ môi giới trực tiếp nên cả dimension và filter `agent` đều bị từ chối. Mỗi query chỉ một fact; dashboard nhiều fact chia query riêng để tránh nhân số dòng. Executor thêm count khóa fact ẩn để phân biệt tập rỗng với giá trị 0 hoặc cost null, không trả cột ẩn qua API. Tỷ lệ giữ Decimal string 0–1 trong API; card/bảng dịch dấu thập phân bằng chuỗi rồi format thành phần trăm (2/9 → 22,22%), không tính lại bằng float.

## Hợp đồng từng KPI

### `sales_value`

- Nguồn duy nhất: `transactions.sale_price`.
- Không gọi là doanh thu, lợi nhuận hay hoa hồng.
- Không cộng `properties.price`, `offers.offer_amount`, appraisal hay repair.
- Empty set: `null` + trạng thái `no_data`.

### `transaction_count`

- Đếm khóa `transaction_id`, không đếm property hoặc dòng sau join nhiều-nhiều.
- Empty set: `0` + trạng thái `no_data`.

### `average_sale_value`

- Tử số và mẫu số dùng cùng filter/scope/kỳ.
- Không lấy trung bình của các trung bình nhóm khi tổng hợp lại.
- Mẫu số 0: `null`, không trả 0 hoặc lỗi chia 0.

### `sold_property_count`

- Đếm `DISTINCT property_id` trong tập transaction đã lọc theo `transaction_date`.
- Một property giao dịch nhiều lần trong kỳ vẫn chỉ tính một.
- Empty set: `0` + trạng thái `no_data`.

### `offer_count`

- Đếm mọi dòng offer trong kỳ, bất kể trạng thái.
- Không suy diễn offer thành transaction.
- Empty set: `0` + trạng thái `no_data`.

### `accepted_offer_rate`

- Tử số: offer có `status = 'accepted'` trong tập đã lọc.
- Mẫu số: **tất cả offer** trong cùng tập/kỳ, không chỉ accepted + rejected.
- Đây không phải tỷ lệ chốt giao dịch; không join `transactions`.
- Mẫu số 0: `null` + trạng thái `no_data`. Tử số 0 nhưng mẫu số > 0: tỷ lệ `0` hợp lệ.

### `repair_cost`

- Chỉ cộng `repairs.cost` theo `repair_date`.
- Không trừ khỏi `sales_value` để tạo lợi nhuận.
- Empty set: `null` + trạng thái `no_data`.

## Dimension và join được phép

| Fact | Join dimension | Mục đích |
|---|---|---|
| `transactions` | `transactions.property_id = properties.property_id` | scope/bang/thành phố |
| `transactions` | `properties.property_type_id = property_types.property_type_id` | loại BĐS |
| `transactions` | `transactions.agent_id` | môi giới; dùng ID hoặc label đã kiểm soát PII |
| `offers` | `offers.property_id = properties.property_id` → `property_types` | scope/bang/thành phố/loại BĐS |
| `repairs` | `repairs.property_id = properties.property_id` → `property_types` | scope/bang/thành phố/loại BĐS |

Không hỗ trợ dimension môi giới cho `offers`/`repairs`: schema không có quan hệ nghiệp vụ trực tiếp hợp lệ. Không dùng `agent_agencies` cho KPI vì 631 giao dịch không khớp và 200 giao dịch khớp nhiều lịch sử đại lý. Không dùng `property_neighbourhoods` vì có fan-out. Đại lý và neighbourhood vì thế chưa là dimension MVP.

## Nội dung không hỗ trợ

- Lợi nhuận, margin, hoa hồng, chỉ tiêu/quota và doanh thu kế toán: dataset không có định nghĩa/cột đầy đủ.
- Tỷ lệ chốt giao dịch từ offer: không có liên kết offer–transaction đủ để xác định chuyển đổi.
- Phân tích sales theo đại lý: lịch sử gán môi giới–đại lý mơ hồ và nhân bản fact.
- So sánh tiền tệ/quy đổi USD: dataset không nêu currency.

Agent phải trả `unsupported` hoặc yêu cầu làm rõ thay vì tự tạo công thức.

## SQL chuẩn và fixture kết quả

- SQL độc lập: `backend/tests/fixtures/golden_queries.sql`.
- Kết quả chuẩn: `backend/tests/fixtures/golden_results.json`.
- Validator độc lập trên các tuple của file import: `backend/tests/test_golden_fixtures.py`.
- Ba query tổng tách riêng sales/offers/repairs, nhận scope và khoảng ngày. Các query dimension cũng giữ riêng fact.
- `accepted_offer_count` và `repair_row_count` là cột chẩn đoán trong fixture để chứng minh mẫu số/grain; chúng không phải KPI bổ sung.
- Với tỷ lệ hoặc trung bình thập phân vô hạn, fraction tử số/mẫu số là oracle chính xác; chuỗi thập phân trong JSON dùng precision 40 để so hồi quy. Tổng tiền được so bằng `Decimal` chính xác đến `0.01`.
- Các fixture mới là baseline file import. Chỉ chạy được logic SQL tương đương độc lập trên file; chưa tuyên bố SQL PostgreSQL đã chạy trên DB thật khi P0-T01 còn bị chặn.

