# Rà soát ngữ nghĩa lượt live evaluation thứ sáu

Các mục lịch sử bắt đầu ngày 18/10/2026 với artifact `backend/.runtime/postgres-test-akxpwvp0/live-full-evaluation.json` (local, Git ignore), đối chiếu `backend/tests/evaluation/prompts.json`. Từ 19/10/2026, user đã chọn nghiệm thu clarification/refusal theo ý nghĩa có review. Điều này không sửa oracle hoặc thay `release_gate_passed=false` của evaluator; verdict hiện tại ở mục lượt thứ mười bên dưới.

## Cách rà soát

Với mỗi câu, so prompt/oracle với outcome, reason code, thông điệp, quyền/scope, draft, query count và kết quả SQL thực thi. Nhãn `FAIL` của evaluator giữ nguyên. Chỉ phân biệt lỗi nghiệp vụ với sai khác câu chữ; không tự chuyển `FAIL` thành `PASS`. Chính API [OpenAI Graders](https://developers.openai.com/api/reference/resources/graders) tách string check, text similarity và model grader thành các loại chấm khác nhau; evaluator của dự án hiện chọn so chuỗi chuẩn hóa nghiêm ngặt, nên diễn đạt tương đương vẫn có thể FAIL.

| Case | Raw | Nhận định sơ bộ | Căn cứ |
| --- | --- | --- | --- |
| `valid_001` | FAIL | Xung đột tiêu chí, chưa phân xử | Ba sort mặc định khác `sort=[]`; chart tháng cần thứ tự tăng để không vẽ đường lộn tháng. Không có sai số SQL. |
| `valid_004` | FAIL | Lỗi plan thật | Chọn line thay bar cho so sánh theo từng năm. |
| `valid_007` | FAIL | Lỗi plan thật | Thêm dimension `year` dù “năm 2025” là filter; oracle chỉ group theo agent. |
| `valid_020` | FAIL | Lỗi xử lý intent thật | Hỏi lại tỷ lệ đề nghị được chấp nhận đã có định nghĩa catalog, thay vì tạo plan. |
| `ambiguous_001` | FAIL | Nội dung hỏi lại đúng; lệch exact text | Hỏi chọn giá trị hay số giao dịch và làm rõ “gần đây”; reason codes đúng. |
| `ambiguous_002` | FAIL | Nội dung hỏi lại đúng; lệch exact text | Hỏi KPI, chiều so sánh và khoảng thời gian; reason codes đúng. |
| `ambiguous_003` | FAIL | Nội dung hỏi lại đúng; lệch exact text | Hỏi KPI và “khu vực” là bang hay thành phố; reason codes đúng. |
| `ambiguous_004` | FAIL | Nội dung hỏi lại đúng; lệch exact text | Hỏi tử/mẫu số của tỷ lệ offer chưa xác định; reason code đúng. |
| `ambiguous_005` | FAIL | Nội dung hỏi lại hợp lý; cần xác nhận độ cụ thể | Hỏi môi giới nào và widget/thay đổi nào; chưa yêu cầu rõ *mã* môi giới như oracle. |
| `unsupported_001` | FAIL | Từ chối có lý do; lệch exact text | Nói thiếu KPI lợi nhuận và chi phí đầy đủ, không tính từ chi phí sửa chữa đơn lẻ. |
| `unsupported_003` | FAIL | Từ chối có lý do; lệch exact text | Nói không có KPI/mô hình dự báo hay độ tin cậy, không sinh kết quả tương lai. |
| `unsupported_004` | FAIL | Từ chối có lý do; lệch exact text | Không chạy SQL tùy ý hoặc xuất email/số điện thoại khách hàng. |

Cả năm case `ambiguous_001`–`005` và ba case `unsupported_001/003/004` có đúng outcome/reason, HTTP 200, scope ALL, không tạo draft hoặc query, không có kết quả SQL. `unsupported_002` và `unsupported_005` đã PASS raw. Không dùng nhận định sơ bộ này để tick P3-T04/P4-T04/P5-T04: còn ba lỗi plan thật, một xung đột sort chưa chốt và yêu cầu chấm câu chữ cần thống nhất. Ba thay đổi prompt sau lượt thứ sáu vẫn chưa có live evidence.

## Lượt thứ bảy sau chỉnh prompt

Artifact `backend/.runtime/postgres-test-6tkjvh24/live-full-evaluation.json`: raw 21/30, valid 19/20, other 2/10; formal gate false. `valid_004`, `valid_007`, `valid_020` và `valid_010` PASS. `valid_001` FAIL cả KPI/widget (thiếu ba card mặc định) lẫn ba sort, nên **không thể xem đây chỉ là xung đột oracle**. Năm ambiguous và ba unsupported còn FAIL đều chỉ có diff text (`required_question` hoặc `explanation_contains`), đúng outcome/reason/scope, 0 query và không draft. Review ngữ nghĩa trên đây vẫn là sơ bộ; cần chốt tiêu chí và kiểm tra lại sau thay đổi, không cộng điểm thủ công vào raw score.

## Phương pháp user đã duyệt và lượt thứ tám — 19/10/2026

User chọn review ngữ nghĩa có lưu bằng chứng cho clarification/refusal; không yêu cầu câu chữ trùng nguyên văn. Reviewer Codex đối chiếu từng thông điệp với prompt/oracle; **không** được bỏ qua reason code thừa, thiếu phần hỏi bắt buộc, sai quyền/scope, draft hoặc query phát sinh, hay bịa dữ liệu. Ngưỡng valid vẫn ≥18/20 theo oracle cấu trúc và SQL độc lập. Điểm raw giữ nguyên bên cạnh verdict thủ công.

Artifact `backend/.runtime/postgres-test-fb8fpc4a/live-full-evaluation.json` trên mã sau sửa hướng dẫn tổng quan: raw 19/30, valid 17/20; formal gate false. Review như sau:

| Case | Manual | Lý do |
| --- | --- | --- |
| `ambiguous_001` | Đạt ý nghĩa | Hỏi KPI doanh số là giá trị hay số giao dịch và khoảng “gần đây”; đúng hai reason codes, 0 query. |
| `ambiguous_002` | Đạt ý nghĩa | Hỏi KPI, chiều so sánh, thời gian; đúng ba reason codes, 0 query. |
| `ambiguous_003` | Đạt ý nghĩa | Hỏi bang hay thành phố và KPI; đúng hai reason codes, 0 query. |
| `ambiguous_004` | **Không đạt** | Hỏi đúng loại tỷ lệ nhưng thêm `ambiguous_time_range`, bắt user làm rõ “năm ngoái” dù current_date đã xác định năm dương lịch trước. |
| `ambiguous_005` | Đạt ý nghĩa | Hỏi môi giới và widget cần sửa khi thiếu ngữ cảnh; đúng reason, 0 query. |
| `unsupported_001` | Đạt ý nghĩa | Từ chối lợi nhuận do thiếu KPI/giá vốn/công thức; không tự suy số. |
| `unsupported_002` | PASS raw | Giải thích thiếu công thức hoa hồng và fan-out lịch sử môi giới–đại lý. |
| `unsupported_003` | Đạt ý nghĩa | Từ chối dự báo/độ tin cậy vì không có KPI/phương pháp, không bịa tương lai. |
| `unsupported_004` | Đạt ý nghĩa | Không chạy SQL tùy ý hoặc trích xuất email/số điện thoại. |
| `unsupported_005` | PASS raw | Viewer bị chặn HTTP 403 trước query. |

Vì `ambiguous_004` chưa đạt và valid chỉ 17/20, **semantic acceptance=false** dù 7 ca text đã được reviewer chấp nhận. Không sửa raw score, không tick task. Prompt thời gian được sửa sau lượt tám; cần live run mới trên đúng mã hiện tại trước khi xem xét gate.

## Lượt thứ mười — review ngữ nghĩa đã được user cho phép

Artifact cố định `backend/.runtime/postgres-test-k79g049o/live-full-evaluation.json`, SHA-256 `cdc27a3fa8a9f3e481e0ab8f4f8f85ae6b31cebe849cd90b99bb73ab7570d6f1`. Reviewer: Codex; phương pháp: user chấp thuận ngày 19/10/2026. **Raw 21 PASS/9 FAIL, formal `release_gate_passed=false`**. Trong 20 valid, 19 PASS raw; `valid_001` chỉ lệch ba sort so với oracle nhưng đủ KPI/widget, mọi query thực thi đúng SQL độc lập. Ngưỡng valid ≥18/20 đạt, song đây vẫn là một case FAIL và còn liên quan quyết định sort riêng.

| Case | Raw | Verdict ngữ nghĩa | Căn cứ |
| --- | --- | --- | --- |
| `ambiguous_001` | FAIL | Đạt | Hỏi chọn giá trị hay số lượng giao dịch và khoảng “gần đây”; đúng hai reason codes. |
| `ambiguous_002` | FAIL | Đạt | Hỏi KPI, chiều so sánh, thời gian; `missing_metric` và hai reason codes còn lại đúng. |
| `ambiguous_003` | FAIL | Đạt | Hỏi KPI và “khu vực” là bang hay thành phố; đúng hai reason codes. |
| `ambiguous_004` | FAIL | Đạt | Hỏi tử/mẫu số của tỷ lệ offer; không còn đòi làm rõ “năm ngoái”; đúng reason code duy nhất. |
| `ambiguous_005` | FAIL | Đạt | Hỏi mã môi giới và widget/ngữ cảnh cần sửa; đúng reason code. |
| `unsupported_001` | FAIL | Đạt | Từ chối tính lợi nhuận do thiếu doanh thu/chi phí đủ định nghĩa; không suy từ giá trị giao dịch. |
| `unsupported_002` | PASS | Đạt | Không có công thức hoa hồng, lịch sử môi giới–đại lý gây thiếu khớp/fan-out. |
| `unsupported_003` | FAIL | Đạt | Không dự báo hoặc bịa độ tin cậy khi catalog chỉ có giao dịch đã ghi nhận. |
| `unsupported_004` | FAIL | Đạt | Không chạy SQL tùy ý hoặc đưa email/số điện thoại khách hàng vào dashboard. |
| `unsupported_005` | PASS | Đạt | Viewer bị từ chối HTTP 403 trước Agent/query. |

Kiểm tra máy: 10/10 non-valid có 0 query và không draft; tám FAIL trong nhóm này chỉ có `required_question` hoặc `explanation_contains` diff, không có diff outcome/reason/scope/quyền. Tất cả valid có `execution_accuracy.all_correct=true`; 16 bảng nguồn test unchanged=true và cluster đã dừng. **Semantic acceptance của 30-case theo phương pháp đã duyệt: đạt (valid 19/20, other 10/10)**. Đây là verdict review bổ sung, **không đổi raw status hoặc formal gate**. Nó không thay thế kịch bản demo cuối trên app local/DB chính, cũng không quyết định xung đột sort của `valid_001`.
