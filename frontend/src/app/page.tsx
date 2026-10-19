"use client";

import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import Link from "next/link";

import {AuthUser, currentUser, logout} from "../lib/api";

type Health = {
  status: string;
  service: string;
  environment: string;
};

export default function Home() {
  const router = useRouter();
  const [health, setHealth] = useState<Health | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadHealth() {
      try {
        const response = await fetch("/api/health", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        setHealth((await response.json()) as Health);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Lỗi không xác định");
        }
      }
    }

    void loadHealth();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let active = true;
    currentUser()
      .then((identity) => {
        if (!active) return;
        if (identity === null) {
          router.replace("/login");
          return;
        }
        setUser(identity);
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Không thể tải phiên đăng nhập.");
        }
      })
      .finally(() => {
        if (active) setAuthReady(true);
      });
    return () => {
      active = false;
    };
  }, [router]);

  async function signOut() {
    setLoggingOut(true);
    setError(null);
    try {
      await logout();
      router.replace("/login");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể đăng xuất.");
      setLoggingOut(false);
    }
  }

  return (
    <main>
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">LOCAL MVP · P1-T01</p>
        <h1 id="page-title">AI Dashboard Agent</h1>
        <p className="lede">
          Nền tảng tạo dashboard bất động sản bằng tiếng Việt đang được khởi tạo.
        </p>
        <div className="userState" aria-live="polite">
          {user ? (
            <>
              <span>
                {user.username} · {user.role} · phạm vi {user.scope_profile}
              </span>
              <button type="button" onClick={signOut} disabled={loggingOut}>
                {loggingOut ? "Đang đăng xuất…" : "Đăng xuất"}
              </button>
            </>
          ) : authReady ? (
            <span>Chưa đăng nhập.</span>
          ) : (
            <span>Đang tải phiên đăng nhập…</span>
          )}
        </div>
        <div className="status" aria-live="polite">
          <span
            className={`statusDot ${health ? "healthy" : error ? "failed" : "loading"}`}
            aria-hidden="true"
          />
          {health
            ? `${health.service} · ${health.status} · ${health.environment}`
            : error
              ? `Không kết nối được backend: ${error}`
              : "Đang kiểm tra backend qua /api/health…"}
        </div>
        {user ? <p><Link href="/dashboards">Mở dashboard đã duyệt →</Link></p> : null}
        {error ? <p className="authError" role="alert">{error}</p> : null}
      </section>
    </main>
  );
}

