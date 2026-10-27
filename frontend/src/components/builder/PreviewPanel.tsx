"use client";

import type {AgentResponse, ConversationView} from "../../lib/api";
import {DashboardGrid} from "../dashboard/DashboardGrid";
import {DraftChanges} from "./DraftChanges";
import {DEMO_CURRENCY_NOTICE} from "../../lib/format";

export function PreviewPanel({conversation, response, busy}: {conversation: ConversationView | null; response: AgentResponse | null; busy: boolean}) {
  const preview = response?.status === "awaiting_approval" ? response : conversation?.preview ?? response;
  const spec = preview?.draft ?? conversation?.draft;
  const hasResults = preview && Object.keys(preview.results ?? {}).length > 0;
  const periods = [...new Set(spec?.queries.map(query => query.plan.date_range?.start
    ? `${query.plan.date_range.start} → trước ${query.plan.date_range.end_exclusive}` : "Toàn bộ khoảng dữ liệu") ?? [])];
  return <section className="previewPanel" aria-label="Bản xem trước dashboard">
    <div className="previewHeading"><h2>Bản xem trước</h2>
      {conversation ? <span className="versionBadge">Bản nháp {conversation.draft_revision}</span> : null}</div>
    <p className="dashboardHint">Dashboard chỉ được chia sẻ sau khi bạn duyệt. Bản đã duyệt tiếp tục phục vụ người xem.</p>
    {response ? <p className={response.status === "failed" ? "authError" : "previewNotice"} role="status">{response.message}</p> : null}
    <DraftChanges changes={preview?.changes} />
    {spec ? <>
      <h3>{spec.title}</h3>
      <p className="dashboardHint">{DEMO_CURRENCY_NOTICE}</p>
      <p className="dashboardHint">Phạm vi: {preview?.scope ?? "theo tài khoản"} · Kỳ: {periods.join("; ")}</p>
      {hasResults || busy ? <DashboardGrid dashboard={{spec}} data={hasResults ? {results: preview.results ?? {}} : null} loading={busy} />
        : <p className="widgetMessage">Gửi yêu cầu để tạo bản xem trước với dữ liệu trong phạm vi hiện tại.</p>}
      {preview?.sql && Object.keys(preview.sql).length > 0 ? <details className="sqlPanel"><summary>Nguồn KPI và SQL của bản nháp</summary>
        {spec.queries.map(query => <section key={query.id}><h3>{query.id}</h3>
          <p>KPI: {query.plan.kpis.join(", ")} · Kỳ: {query.plan.date_range?.start ?? "toàn bộ khoảng dữ liệu"}{query.plan.date_range?.end_exclusive ? ` → trước ${query.plan.date_range.end_exclusive}` : ""}</p>
          <pre>{preview.sql?.[query.id]}</pre></section>)}
      </details> : null}
    </> : <div className="previewEmpty"><p>Dashboard của bạn sẽ xuất hiện ở đây.</p><p className="dashboardHint">Nêu rõ chỉ tiêu, kỳ thời gian và cách phân nhóm để bắt đầu.</p></div>}
  </section>;
}
