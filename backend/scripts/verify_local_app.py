"""Smoke the running local frontend proxy and four seeded demo logins."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import dotenv_values
import httpx


BACKEND = Path(__file__).resolve().parents[1]
SECRETS = BACKEND / ".runtime" / "local-demo-accounts.env"
ACCOUNTS = {
    "builder_all": ("SEED_BUILDER_ALL_PASSWORD", "builder", "ALL"),
    "viewer_all": ("SEED_VIEWER_ALL_PASSWORD", "viewer", "ALL"),
    "viewer_ca": ("SEED_VIEWER_CA_PASSWORD", "viewer", "CA"),
    "builder_tx": ("SEED_BUILDER_TX_PASSWORD", "builder", "TX"),
}
EXPECTED_SALES_COUNTS = {"ALL": 1000, "CA": 135, "TX": 132}
ORIGIN = "http://localhost:3000"


def main() -> int:
    if not SECRETS.is_file():
        print("local app verification unavailable: private demo password artifact missing")
        return 2
    passwords = dotenv_values(SECRETS, interpolate=False)
    if any(not passwords.get(variable) for variable, _role, _scope in ACCOUNTS.values()):
        print("local app verification unavailable: a demo password is missing")
        return 2
    with httpx.Client(base_url=ORIGIN, timeout=10) as client:
        health = client.get("/api/health")
        if health.status_code != 200 or health.json().get("status") != "ok":
            raise RuntimeError("frontend proxy health check failed")
    verified = []
    viewer_counts = {}
    for username, (variable, role, scope) in ACCOUNTS.items():
        with httpx.Client(base_url=ORIGIN, timeout=10) as client:
            login = client.post("/api/auth/login", json={
                "username": username, "password": passwords[variable],
            }, headers={"Origin": ORIGIN})
            if login.status_code != 200:
                raise RuntimeError("demo login failed")
            me = client.get("/api/auth/me")
            if (me.status_code != 200 or me.json() != {
                    "username": username, "role": role, "scope_profile": scope}):
                raise RuntimeError("demo session identity mismatch")
            dashboards = client.get("/api/dashboards")
            if dashboards.status_code != 200 or not isinstance(dashboards.json(), list):
                raise RuntimeError("authenticated dashboard list failed")
            items = [item for item in dashboards.json()
                     if item.get("title") == "Tổng quan giao dịch bất động sản"]
            if len(items) != 1:
                raise RuntimeError("expected one identifiable CLI-seeded dashboard")
            dashboard_id = items[0]["id"]
            queried = client.post(f"/api/dashboards/{dashboard_id}/query", json={},
                                  headers={"Origin": ORIGIN})
            if queried.status_code != 200:
                raise RuntimeError("published dashboard query failed")
            payload = queried.json()
            count = payload["results"]["sales"]["rows"][0]["transaction_count"]
            if payload["scope"] != scope or count != EXPECTED_SALES_COUNTS[scope]:
                raise RuntimeError("published dashboard scope or KPI mismatch")
            viewer_counts[username] = count
            logout = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
            if logout.status_code != 204:
                raise RuntimeError("demo logout failed")
            verified.append(username)
    print(json.dumps({"frontend_proxy_healthy": True, "demo_logins_verified": verified,
                      "scoped_transaction_counts": viewer_counts,
                      "all_checks_passed": len(verified) == 4}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"local app smoke failed ({type(error).__name__}); secrets and responses suppressed")
        raise SystemExit(1) from None
