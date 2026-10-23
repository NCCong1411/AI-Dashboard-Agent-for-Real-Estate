"""Sync the local Viewer file without PowerShell variables or plaintext CLI secrets."""

import argparse
import getpass
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--old-username", default="viewer_all")
    parser.add_argument("--prompt-password", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("No changes made. Use --apply to update the ALL Viewer and revoke its sessions.")
        return
    credentials = ROOT / "backend/.runtime/lightsail-viewer.env"
    if not credentials.is_file():
        parser.error("Missing backend/.runtime/lightsail-viewer.env")
    if args.prompt_password:
        from dotenv import set_key
        password = getpass.getpass("New Viewer password: ")
        if not 12 <= len(password) <= 1024 or "\r" in password or "\n" in password:
            parser.error("Password must have 12-1024 characters without newlines")
        set_key(str(credentials), "DEMO_PASSWORD", password)
        if os.name == "nt":
            user = subprocess.check_output(["whoami"], text=True).strip()
            subprocess.run(["icacls", str(credentials), "/inheritance:r", "/grant:r",
                            f"{user}:F"], check=True, capture_output=True)
        else:
            credentials.chmod(0o600)
    helper = ROOT / "deploy/scripts/lightsail_remote.py"

    def remote(*arguments):
        subprocess.run([sys.executable, str(helper), *arguments], cwd=ROOT, check=True)

    remote("upload", str(credentials), "/opt/ai-dashboard/deploy/secrets/viewer-update.env")
    remote("upload", str(ROOT / "backend/scripts/update_viewer_credentials.py"),
           "/opt/ai-dashboard/deploy/secrets/update_viewer_credentials.py")
    command = "\n".join([
        "set -eu",
        "cd /opt/ai-dashboard",
        "chmod 600 deploy/secrets/viewer-update.env",
        "sudo docker cp deploy/secrets/viewer-update.env ai-dashboard-agent-backend-1:/tmp/viewer-update.env",
        "sudo docker cp deploy/secrets/update_viewer_credentials.py ai-dashboard-agent-backend-1:/app/backend/scripts/update_viewer_credentials.py",
        "sudo docker compose -f compose.prod.yaml -f compose.images.yaml exec -T backend python scripts/update_viewer_credentials.py --input /tmp/viewer-update.env --old-username " + shlex.quote(args.old_username),
        "mv deploy/secrets/viewer-update.env deploy/secrets/viewer-demo.env",
    ])
    remote("ssh", command)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit("Sync did not complete. Check the preceding error; do not retry blindly after a connection loss.")
