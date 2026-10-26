"use client";

import type {ReactNode} from "react";
import Link from "next/link";
import {usePathname, useRouter} from "next/navigation";
import {useState} from "react";
import {logout, type AuthUser} from "../../lib/api";
import {Icon} from "./Icon";

export function WorkspaceShell({user, children}: {user: AuthUser | null; children: ReactNode}) {
  const pathname = usePathname();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const builder = pathname.startsWith("/builder");
  async function signOut() {
    setBusy(true); setError("");
    try {await logout(); router.replace("/login");}
    catch {setError("Không thể đăng xuất. Vui lòng thử lại."); setBusy(false);}
  }
  const navigation = <>
    <Link href="/dashboards" className={`navItem ${!builder ? "active" : ""}`} aria-current={!builder ? "page" : undefined}>
      <Icon name="grid" /> Dashboard</Link>
    {user?.role === "builder" ? <Link href="/builder" className={`navItem ${builder ? "active" : ""}`} aria-current={builder ? "page" : undefined}>
      <Icon name="sparkles" /> AI Builder</Link> : null}
  </>;
  return <div className="appFrame">
    <aside className="appSidebar" aria-label="Thanh bên">
      <Link href="/dashboards" className="brand"><span className="brandMark"><Icon name="building" size={23} /></span>
        <span>AI Dashboard<small>REAL ESTATE ANALYTICS</small></span></Link>
      <p className="navCaption">KHÔNG GIAN LÀM VIỆC</p>
      <nav aria-label="Điều hướng chính" className="appNav">{navigation}</nav>
      <div className="sidebarNote"><span className="noteIcon"><Icon name="chart" size={24} /></span>
        <h2>Dữ liệu thành góc nhìn.</h2><p>Khám phá chỉ số và tìm câu chuyện phía sau mỗi giao dịch.</p></div>
      {user ? <div className="sidebarAccount"><span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span>
        <span><strong>{user.username}</strong><small>{user.role === "builder" ? "Builder · Tạo & duyệt" : "Viewer · Chỉ xem"}</small></span>
        <span className="accountDot" aria-label="Đã đăng nhập" /></div> : null}
    </aside>
    <div className="appMain">
      <header className="appTopbar"><span className="topbarContext"><Icon name={builder ? "sparkles" : "grid"} size={17} />
        Không gian làm việc <span className="breadcrumbDivider">/</span><strong>{builder ? "AI Builder" : "Dashboard"}</strong></span>
        {user ? <div className="topbarActions"><span className="scopePill"><span /> Phạm vi {user.scope_profile}</span>
          <button className="signOutButton" onClick={signOut} disabled={busy}><Icon name="logout" size={17} /><span>Đăng xuất</span></button></div> : null}</header>
      <nav className="mobileNav" aria-label="Điều hướng di động">{navigation}</nav>
      <main className="dashboardPage">{error ? <p className="authError" role="alert">{error}</p> : null}{children}</main>
      <footer className="appFooter"><span>AI Dashboard Agent</span><span>Góc nhìn rõ ràng hơn từ dữ liệu bất động sản.</span></footer>
    </div>
  </div>;
}
