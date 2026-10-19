"use client";

import {FormEvent, useState} from "react";
import {useRouter} from "next/navigation";

import {login} from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <main>
      <section className="hero authPanel" aria-labelledby="login-title">
        <p className="eyebrow">AI DASHBOARD AGENT</p>
        <h1 id="login-title">Đăng nhập</h1>
        <form className="authForm" onSubmit={submit}>
          <label>
            Tên đăng nhập
            <input name="username" autoComplete="username" required maxLength={128} />
          </label>
          <label>
            Mật khẩu
            <input name="password" type="password" autoComplete="current-password" required />
          </label>
          {error ? <p className="authError" role="alert">{error}</p> : null}
          <button type="submit" disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}</button>
        </form>
      </section>
    </main>
  );
}
