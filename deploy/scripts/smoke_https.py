"""Read-only dashboard smoke checks over real HTTPS; login/logout use demo users.

Run on the VPS from the release directory. No credentials are printed.
"""

import http.cookiejar
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


def main():
    private = Path("deploy/secrets")
    domain = (private / "site.env").read_text().strip().split("=", 1)[1]
    origin = "https://" + domain
    accounts = dict(line.split("=", 1) for line in
                    (private / "accounts.env").read_text().splitlines() if line)
    outcomes = {}
    for username, key, scope in [
        ("viewer_all", "SEED_VIEWER_ALL_PASSWORD", "ALL"),
        ("viewer_ca", "SEED_VIEWER_CA_PASSWORD", "CA"),
        ("builder_tx", "SEED_BUILDER_TX_PASSWORD", "TX"),
    ]:
        client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, payload=None):
            data = None if payload is None else json.dumps(payload).encode()
            return client.open(urllib.request.Request(
                origin + path, data=data,
                headers={"Origin": origin, "Content-Type": "application/json"}),
                timeout=45)

        with request("/api/auth/login", {"username": username,
                                        "password": accounts[key]}) as response:
            cookie = response.headers.get("Set-Cookie", "").lower()
            assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
            assert json.load(response)["scope_profile"] == scope
        with request("/api/dashboards") as response:
            dashboards = json.load(response)
            assert dashboards
        dashboard_id = dashboards[0]["id"]
        with request("/api/dashboards/" + dashboard_id) as response:
            detail = json.load(response)
            if username.startswith("viewer"):
                assert detail["can_edit"] is False and "sql" not in detail
        with request("/api/dashboards/" + dashboard_id + "/query", {}) as response:
            result = json.load(response)
            assert result["scope"] == scope
            assert result["results"]
            assert all(value["status"] in ("ok", "no_data")
                       for value in result["results"].values())
        if username.startswith("viewer"):
            try:
                request("/api/conversations/00000000-0000-0000-0000-000000000000")
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError("Viewer must not access Builder conversations")
        with request("/api/auth/logout", {}) as response:
            assert response.status == 204
        try:
            request("/api/auth/me")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Session must be invalid after logout")
        outcomes[scope] = "passed"
    viewer_file = private / "viewer-demo.env"
    if not viewer_file.exists():
        with os.fdopen(os.open(viewer_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8") as output:
            output.write(f"DEMO_URL={origin}\nDEMO_USERNAME=viewer_all\n")
            output.write("DEMO_PASSWORD=" + accounts["SEED_VIEWER_ALL_PASSWORD"] + "\n")
    print(json.dumps({"https_smoke": outcomes, "viewer_credentials_file": str(viewer_file),
                      "secrets_printed": False}))


if __name__ == "__main__":
    main()
