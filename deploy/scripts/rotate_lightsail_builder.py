"""Rotate the deployed Builder and update private seed/local credential files."""

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]

HOST_FINISH = '''
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import urllib.request

private = Path("deploy/secrets")
source = private / "builder-password-update.txt"
password = source.read_text(encoding="utf-8")
target = private / "accounts.env"
lines = target.read_text(encoding="utf-8").splitlines()
assert sum(line.startswith("SEED_BUILDER_ALL_PASSWORD=") for line in lines) == 1
replacement = "SEED_BUILDER_ALL_PASSWORD=" + json.dumps(password, ensure_ascii=False)
document = "\\n".join(replacement if line.startswith("SEED_BUILDER_ALL_PASSWORD=") else line for line in lines) + "\\n"
with tempfile.NamedTemporaryFile(mode="w", dir=private, encoding="utf-8", delete=False) as stream:
    temporary = Path(stream.name)
    os.chmod(temporary, 0o600)
    stream.write(document)
os.replace(temporary, target)
source.unlink()
origin = "https://" + (private / "site.env").read_text().strip().split("=", 1)[1]
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path, payload):
    return client.open(urllib.request.Request(origin + path,
        data=json.dumps(payload).encode(), headers={"Origin": origin, "Content-Type": "application/json"}), timeout=30)
with request("/api/auth/login", {"username": "builder_all", "password": password}) as response:
    user = json.load(response)
    assert user["role"] == "builder" and user["scope_profile"] == "ALL"
with request("/api/auth/logout", {}) as response:
    assert response.status == 204
print("Builder HTTPS login/logout verified; private seed credential updated.")
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("No changes made. Use --apply to rotate builder_all.")
        return
    if args.password_stdin:
        print("Waiting for password on stdin (not echoed).", flush=True)
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New Builder password: ")
    if not 12 <= len(password) <= 1024 or "\n" in password or "\r" in password:
        parser.error("Password must have 12-1024 characters without newlines")

    def remote(*arguments):
        subprocess.run([sys.executable, str(ROOT / "deploy/scripts/lightsail_remote.py"),
                        *arguments], cwd=ROOT, check=True)

    with tempfile.TemporaryDirectory(prefix="ai-dashboard-builder-") as directory:
        root = Path(directory)
        if os.name == "nt":
            user = subprocess.check_output(["whoami"], text=True).strip()
            subprocess.run(["icacls", str(root), "/inheritance:r", "/grant:r",
                            f"{user}:(OI)(CI)F"], check=True, capture_output=True)
        secret = root / "password.txt"
        secret.write_text(password, encoding="utf-8")
        secret.chmod(0o600)
        remote("upload", str(secret), "/opt/ai-dashboard/deploy/secrets/builder-password-update.txt")
    remote("upload", str(ROOT / "backend/scripts/rotate_builder_password.py"),
           "/opt/ai-dashboard/deploy/secrets/rotate_builder_password.py")
    command = "\n".join([
        "set -eu", "cd /opt/ai-dashboard",
        "chmod 600 deploy/secrets/builder-password-update.txt",
        "sudo docker cp deploy/secrets/builder-password-update.txt ai-dashboard-agent-backend-1:/tmp/builder-password-update.txt",
        "sudo docker cp deploy/secrets/rotate_builder_password.py ai-dashboard-agent-backend-1:/app/backend/scripts/rotate_builder_password.py",
        "sudo docker compose -f compose.prod.yaml -f compose.images.yaml exec -T backend python scripts/rotate_builder_password.py --input /tmp/builder-password-update.txt",
        "python3 - <<'PY'", HOST_FINISH, "PY",
    ])
    remote("ssh", command)
    destination = ROOT / "backend/.runtime/lightsail-accounts.env"
    remote("download", "/opt/ai-dashboard/deploy/secrets/accounts.env", str(destination))
    if os.name == "nt":
        user = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(destination), "/inheritance:r", "/grant:r",
                        f"{user}:F"], check=True, capture_output=True)
    else:
        destination.chmod(0o600)
    print("Local private account file synchronized; no password printed.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit("Rotation workflow interrupted. Check state before retrying; DB may already be updated.")
