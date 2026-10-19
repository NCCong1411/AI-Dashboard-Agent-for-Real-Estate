"use client";

import Link from "next/link";
import type {ApprovalRequest, ConversationView} from "../../lib/api";

export function ApprovalPanel({conversation, busy, onDecide}: {
  conversation: ConversationView | null; busy: boolean;
  onDecide: (decision: ApprovalRequest["decision"]) => Promise<void>;
}) {
  if (!conversation) return null;
  const receipt = conversation.approval;
  if (receipt) return <section className="approvalPanel" aria-label="Quyết định bản nháp">
    <h2>{receipt.decision === "approve" ? "Đã phát hành dashboard" : "Đã từ chối bản nháp"}</h2>
    {receipt.dashboard_id ? <Link className="backLink" href={`/dashboards/${encodeURIComponent(receipt.dashboard_id)}`}>
      Mở dashboard phiên bản {receipt.version_number} →
    </Link> : <p>Bản nháp này chưa được phát hành. Bạn có thể tiếp tục chỉnh sửa trong hội thoại.</p>}
    {!receipt.checkpoint_synced ? <>
      <p role="status">Quyết định đã được lưu. Hoàn tất xử lý để tiếp tục chỉnh sửa.</p>
      <button disabled={busy} onClick={() => void onDecide(receipt.decision)}>Hoàn tất xử lý</button>
    </> : null}
  </section>;
  if (conversation.status !== "awaiting_approval" || !conversation.draft_hash) return null;
  return <section className="approvalPanel" aria-label="Duyệt bản nháp">
    <h2>Duyệt bản nháp {conversation.draft_revision}</h2>
    <p>{conversation.base_version_number == null
      ? "Phát hành dashboard mới để người xem truy cập theo quyền của họ."
      : `Phát hành phiên bản mới từ phiên bản ${conversation.base_version_number}.`}</p>
    <div className="approvalActions">
      <button disabled={busy} onClick={() => void onDecide("approve")}>Duyệt và phát hành</button>
      <button className="secondaryAction" disabled={busy} onClick={() => void onDecide("reject")}>Từ chối bản nháp</button>
    </div>
  </section>;
}
