"use client";

import Link from "next/link";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {ApiError, AuthUser, currentUser, DashboardSummary, listDashboards} from "../../lib/api";
import {WorkspaceShell} from "../../components/ui/WorkspaceShell";
import {Icon} from "../../components/ui/Icon";

export default function DashboardsPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [items, setItems] = useState<DashboardSummary[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const identity = await currentUser();
        if (!identity) {router.replace("/login"); return;}
        const dashboards = await listDashboards(controller.signal);
        if (!controller.signal.aborted) {setUser(identity); setItems(dashboards);}
      } catch (reason) {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) router.replace("/login");
        else setError(reason instanceof Error ? reason.message : "Không thể tải dashboard.");
      }
    }
    void load();
    return () => controller.abort();
  }, [router]);
  return <WorkspaceShell user={user}><div className="dashboardShell">
    <header className="dashboardHeader"><div><p className="eyebrow">TỔNG QUAN KHÔNG GIAN LÀM VIỆC</p><h1>Thư viện dashboard</h1></div>
      {user?.role === "builder" ? <Link className="builderLink" href="/builder"><Icon name="sparkles" size={17} />Tạo dashboard bằng hội thoại →</Link> : <span className="versionBadge"><Icon name="shield" size={15} /> Chế độ xem</span>}</header>
    <p className="dashboardHint">Các dashboard đã duyệt. Dữ liệu được tính theo quyền truy cập của bạn.</p>
    {items && user ? <div className="libraryOverview"><div><span>Dashboard đã duyệt</span><strong>{items.length.toLocaleString("vi-VN")}<Icon name="grid" size={20} /></strong></div>
      <div><span>Phạm vi dữ liệu</span><strong>{user.scope_profile}<Icon name="filter" size={20} /></strong></div>
      <div><span>Quyền truy cập</span><strong>{user.role === "builder" ? "Builder" : "Viewer"}<Icon name="shield" size={20} /></strong></div></div> : null}
    <div className="libraryHeading"><h2>Dashboard đã phát hành</h2><span>Chọn một dashboard để bắt đầu khám phá</span></div>
    {error ? <p role="alert" className="authError">{error}</p> : items === null ? <p role="status">Đang tải dashboard…</p>
      : items.length === 0 ? <p>Chưa có dashboard được duyệt.</p>
      : <div className="dashboardList">{items.map(item => <Link key={item.id} href={`/dashboards/${item.id}`} className="dashboardLink">
        <div className="dashboardCardTop"><span className="dashboardCardIcon"><Icon name="chart" size={25} /></span><span className="publishedPill"><Icon name="check" size={13} /> Đã duyệt</span></div>
        <h2>{item.title}</h2><p>Phiên bản {item.version_number} · Đã duyệt {new Date(item.approved_at).toLocaleDateString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh"})}</p><span className="dashboardCardAction">Mở dashboard <Icon name="arrow" size={18} /></span>
      </Link>)}</div>}
  </div></WorkspaceShell>;
}
