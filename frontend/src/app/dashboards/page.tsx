"use client";

import Link from "next/link";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {ApiError, AuthUser, currentUser, DashboardSummary, listDashboards, logout} from "../../lib/api";

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
  async function signOut() {
    try {await logout(); router.replace("/login");}
    catch {setError("Không thể đăng xuất. Vui lòng thử lại.");}
  }
  return <main className="dashboardPage"><div className="dashboardShell">
    <header className="dashboardHeader"><div><p className="eyebrow">KHÔNG GIAN LÀM VIỆC</p><h1>Dashboard</h1></div>
      {user ? <div className="userState"><span>{user.username} · phạm vi {user.scope_profile}</span><button onClick={signOut}>Đăng xuất</button></div> : null}</header>
    <p className="dashboardHint">Các dashboard đã duyệt. Dữ liệu được tính theo quyền truy cập của bạn.</p>
    {user?.role === "builder" ? <Link className="builderLink" href="/builder">Tạo dashboard bằng hội thoại →</Link> : null}
    {error ? <p role="alert" className="authError">{error}</p> : items === null ? <p role="status">Đang tải dashboard…</p>
      : items.length === 0 ? <p>Chưa có dashboard được duyệt.</p>
      : <div className="dashboardList">{items.map(item => <Link key={item.id} href={`/dashboards/${item.id}`} className="dashboardLink">
        <h2>{item.title}</h2><p>Phiên bản {item.version_number} · Đã duyệt {new Date(item.approved_at).toLocaleDateString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh"})}</p><span>Mở dashboard →</span>
      </Link>)}</div>}
  </div></main>;
}
