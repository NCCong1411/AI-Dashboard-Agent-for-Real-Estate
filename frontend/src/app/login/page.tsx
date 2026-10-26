"use client";

import {FormEvent, useState} from "react";
import {useRouter} from "next/navigation";

import {login} from "../../lib/api";
import {Icon} from "../../components/ui/Icon";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await login(String(form.get("username") ?? ""), String(form.get("password") ?? ""));
      router.replace("/dashboards");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể đăng nhập.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="authPage">
      <header className="authHeader"><span className="brand"><span className="brandMark"><Icon name="building" size={23} /></span>
        <span>AI Dashboard<small>REAL ESTATE ANALYTICS</small></span></span><span className="authHeaderNote">Không gian phân tích bất động sản</span></header>
      <div className="authShell">
        <section className="authShowcase" aria-label="Giới thiệu">
          <span className="showcaseTag"><Icon name="sparkles" size={15} /> GÓC NHÌN TỪ DỮ LIỆU</span>
          <h2>Hiểu dữ liệu.<br />Nhìn rõ cơ hội.</h2>
          <p>Tập hợp những chỉ số quan trọng trong một không gian phân tích trực quan, rõ ràng và dễ khám phá.</p>
          <div className="authIllustration" aria-hidden="true"><svg viewBox="0 0 420 210" fill="none">
            <path d="M30 181h360" stroke="currentColor" strokeWidth="1.5" />
            <path d="M73 180V82l83-34v132 M156 97h74v83 M259 180V34l82 28v118" fill="#fff8df" stroke="currentColor" strokeWidth="1.5" />
            <path d="M91 94h15m18-10h15m-48 37h15m18-10h15m-48 37h15m18-10h15m38-11h14m15 0h14m-43 25h14m15 0h14m67-87h14m15 5h14m-43 19h14m15 5h14m-43 19h14m15 5h14m-43 19h14m15 5h14" stroke="currentColor" strokeWidth="3" />
            <circle cx="235" cy="54" r="18" fill="#f4d675" /><path d="m226 54 6 6 12-13" stroke="currentColor" strokeWidth="2" />
          </svg><span className="illustrationLabel"><span /> BẤT ĐỘNG SẢN, QUA LĂNG KÍNH DỮ LIỆU</span></div>
          <div className="showcaseFeatures"><span><Icon name="chart" size={17} /> Dashboard trực quan</span><span><Icon name="shield" size={17} /> Phân quyền truy cập</span></div>
        </section>
      <section className="authPanel" aria-labelledby="login-title">
        <span className="authWelcomeIcon"><Icon name="grid" size={24} /></span>
        <p className="eyebrow">CHÀO MỪNG TRỞ LẠI</p>
        <h1 id="login-title">Đăng nhập</h1>
        <p className="authDescription">Tiếp tục khám phá dashboard và dữ liệu của bạn.</p>
        <form className="authForm" onSubmit={submit}>
          <label>
            Tên đăng nhập
            <input name="username" autoComplete="username" required maxLength={128} placeholder="Nhập tên đăng nhập" />
          </label>
          <label>
            Mật khẩu
            <span className="passwordField"><input name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" required placeholder="Nhập mật khẩu" />
              <button className="passwordToggle" type="button" onClick={() => setShowPassword(value => !value)}
                aria-label={showPassword ? "Ẩn ký tự" : "Hiện ký tự"} title={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} aria-pressed={showPassword}><Icon name={showPassword ? "eyeOff" : "eye"} size={19} /></button></span>
          </label>
          {error ? <p className="authError" role="alert">{error}</p> : null}
          <button className="authSubmit" type="submit" disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}<Icon name="arrow" size={19} /></button>
        </form>
        <p className="authPrivacy"><Icon name="shield" size={15} /> Dữ liệu hiển thị theo quyền truy cập của bạn.</p>
      </section>
      </div>
      <footer className="authFooter">AI Dashboard Agent <span>·</span> Phân tích bất động sản</footer>
    </main>
  );
}
