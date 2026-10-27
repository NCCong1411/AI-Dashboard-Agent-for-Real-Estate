"use client";

import Link from "next/link";
import {useParams, useRouter} from "next/navigation";
import {useCallback, useEffect, useRef, useState} from "react";
import {ApiError, type AuthUser, currentUser, DashboardDetail, DashboardQueryRequest, DashboardQueryResponse, getDashboard, queryDashboard} from "../../../lib/api";
import {DashboardGrid} from "../../../components/dashboard/DashboardGrid";
import {Filters} from "../../../components/dashboard/Filters";
import {WorkspaceShell} from "../../../components/ui/WorkspaceShell";
import {Icon} from "../../../components/ui/Icon";
import {DEMO_CURRENCY_NOTICE} from "../../../lib/format";

export default function DashboardPage() {
  const {id} = useParams<{id: string}>();
  const router = useRouter();
  const [dashboard, setDashboard] = useState<DashboardDetail | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [data, setData] = useState<DashboardQueryResponse | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState<DashboardQueryRequest>({});
  const controller = useRef<AbortController | null>(null);
  const dashboardSnapshot = useRef<DashboardDetail | null>(null);
  const load = useCallback(async (request: DashboardQueryRequest) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true); setError(""); setData(null);
    try {
      let result = await queryDashboard(id, request, current.signal);
      let detail = dashboardSnapshot.current;
      // A publish can occur between metadata loading and data refresh. Match
      // the spec to the returned version before rendering its query results.
      for (let attempt = 0; attempt < 2 && (detail?.id !== id || detail.version_number !== result.version_number); attempt++) {
        detail = await getDashboard(id, current.signal);
        if (detail.version_number !== result.version_number) result = await queryDashboard(id, request, current.signal);
      }
      if (!detail || detail.id !== id || detail.version_number !== result.version_number) {
        throw new Error("Dashboard đang được cập nhật. Hãy tải lại dữ liệu.");
      }
      if (!current.signal.aborted) {
        dashboardSnapshot.current = detail;
        setDashboard(detail); setData(result); setFilters(request);
      }
    } catch (reason) {
      if (current.signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 401) {setDashboard(null); router.replace("/login");}
      else setError(reason instanceof Error ? reason.message : "Không thể tải dữ liệu.");
    } finally {
      if (!current.signal.aborted) setBusy(false);
    }
  }, [id, router]);
  useEffect(() => {
    let active = true;
    const initial = new AbortController();
    async function open() {
      try {
        const identity = await currentUser();
        if (!active) return;
        if (!identity) {router.replace("/login"); return;}
        setUser(identity);
        const detail = await getDashboard(id, initial.signal);
        if (!active) return;
        dashboardSnapshot.current = detail;
        setDashboard(detail);
        await load({});
      } catch (reason) {
        if (active) {setError(reason instanceof Error ? reason.message : "Không thể mở dashboard."); setBusy(false);}
      }
    }
    void open();
    return () => {active = false; initial.abort(); controller.current?.abort();};
  }, [id, router, load]);
  return <WorkspaceShell user={user}><div className="dashboardShell">
    <Link href="/dashboards" className="backLink">← Dashboard đã duyệt</Link>
    <header className="dashboardHeader"><div><p className="eyebrow">PHÂN TÍCH BẤT ĐỘNG SẢN</p><h1>{dashboard?.title ?? "Dashboard"}</h1></div>
      {dashboard ? <span className="versionBadge"><Icon name="check" size={15} /> Phiên bản {dashboard.version_number} · Đã duyệt</span> : null}</header>
    {dashboard ? <>
      {dashboard.can_edit ? <p><Link className="builderLink" href={`/builder?dashboard=${encodeURIComponent(id)}`}>Chỉnh sửa bằng hội thoại →</Link></p> : null}
      <Filters busy={busy} onApply={request => void load(request)} />
      <div className="dashboardContext"><p>Phạm vi: {data?.scope ?? "theo tài khoản"} · {filters.date_range?.start
        ? `Bộ lọc ngày từ ${filters.date_range.start} đến trước ${filters.date_range.end_exclusive}` : "Toàn bộ khoảng dữ liệu được lưu"}</p>
        <button disabled={busy} onClick={() => void load(filters)}><Icon name="refresh" size={16} />Tải lại dữ liệu</button></div>
      {error ? <p className="authError" role="alert">{error}</p> : <DashboardGrid dashboard={dashboard} data={data} loading={busy} />}
      <p className="dashboardHint">Kỳ và bộ lọc bổ sung được giao với định nghĩa đã duyệt. {DEMO_CURRENCY_NOTICE}</p>
      {dashboard.sql ? <details className="sqlPanel"><summary>Nguồn KPI và SQL</summary>{dashboard.spec.queries.map(query => <section key={query.id}>
        <h2>{query.id}</h2><p>KPI: {query.plan.kpis.join(", ")} · Kỳ: {query.plan.date_range?.start ?? "toàn bộ dữ liệu"}{query.plan.date_range?.end_exclusive ? ` → trước ${query.plan.date_range.end_exclusive}` : ""}</p><pre>{dashboard.sql?.[query.id]}</pre>
      </section>)}</details> : null}
    </> : error ? <p className="authError" role="alert">{error}</p> : <p role="status">Đang mở dashboard…</p>}
  </div></WorkspaceShell>;
}
