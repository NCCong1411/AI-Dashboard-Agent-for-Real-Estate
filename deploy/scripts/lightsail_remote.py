"""Run SSH/SCP with short-lived Lightsail credentials and AWS-verified host keys.

Credentials are held in a temporary, private directory, never logged or committed.
Usage: python deploy/scripts/lightsail_remote.py ssh 'remote command'
       python deploy/scripts/lightsail_remote.py upload local-path remote-path
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def executable(name):
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if os.name == "nt" and name == "aws":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Amazon/AWSCLIV2/aws.exe"
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(f"Cannot find {name}. Install it or reopen the terminal to refresh PATH.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["ssh", "upload", "download"])
    parser.add_argument("arguments", nargs="+")
    args = parser.parse_args()
    if len(args.arguments) != (1 if args.operation == "ssh" else 2):
        parser.error("ssh requires one command; upload requires source and destination")
    if args.operation == "ssh" and args.arguments[0] == "$syncCommand":
        parser.error("$syncCommand is a PowerShell variable. Open PowerShell and run its full definition first.")
    aws = executable("aws")
    transport = executable("ssh" if args.operation == "ssh" else "scp")
    response = subprocess.run([
        aws, "lightsail", "get-instance-access-details", "--instance-name",
        "ai-dashboard-mvp", "--region", "us-east-1", "--protocol", "ssh",
        "--output", "json", "--no-cli-pager",
    ], check=True, capture_output=True, text=True)
    details = json.loads(response.stdout)["accessDetails"]
    if details["instanceName"] != "ai-dashboard-mvp" or not details.get("hostKeys"):
        raise RuntimeError("Missing AWS-verified host identity")
    with tempfile.TemporaryDirectory(prefix="ai-dashboard-ssh-") as directory:
        root = Path(directory)
        if os.name == "nt":
            user = subprocess.check_output(["whoami"], text=True).strip()
            subprocess.run(["icacls", str(root), "/inheritance:r", "/grant:r",
                            f"{user}:(OI)(CI)F"], check=True, capture_output=True)
        key = root / "identity"
        key.write_text(details["privateKey"], encoding="utf-8")
        key.chmod(0o600)
        (root / "identity-cert.pub").write_text(details["certKey"], encoding="utf-8")
        host = details["ipAddress"]
        known = root / "known_hosts"
        known.write_text("".join(
            f"{host} {item['algorithm']} {item['publicKey']}\n"
            for item in details["hostKeys"]
        ), encoding="utf-8")
        options = ["-i", str(key), "-o", f"UserKnownHostsFile={known}",
                   "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=15"]
        destination = f"{details['username']}@{host}"
        if args.operation == "ssh":
            command = [transport, *options, destination, "sh -se"]
            # Binary stdin avoids Windows text-mode LF -> CRLF translation.
            script = args.arguments[0].replace("\r\n", "\n").replace("\r", "\n")
            raise SystemExit(subprocess.run(
                command, input=(script + "\n").encode("utf-8"),
            ).returncode)
        elif args.operation == "upload":
            command = [transport, *options, args.arguments[0],
                       f"{destination}:{args.arguments[1]}"]
        else:
            command = [transport, *options, f"{destination}:{args.arguments[0]}",
                       args.arguments[1]]
        raise SystemExit(subprocess.run(command).returncode)


if __name__ == "__main__":
    main()
