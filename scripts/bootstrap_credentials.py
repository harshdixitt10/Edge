"""
Install-time credential bootstrap.

Run by install.bat (and install.sh) right after dependencies are installed.
Idempotent: only writes a fresh hash when default_password_hash is empty or
--force is passed.

Effect:
  1. Computes bcrypt hash for the default password ("changeme") with the same
     parameters AuthManager uses at runtime.
  2. Writes the hash into config.yaml.
  3. Writes .credentials_backup.yaml with base64-encoded admin / changeme.
  4. Prints the credentials so the operator sees them in the installer log.

Why this exists: previously the template shipped with a hardcoded bcrypt hash
that did not match "changeme", so a fresh install gave "Invalid credentials"
on the first login attempt. This script removes that footgun by deriving the
hash on the install machine itself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the edge_server package importable when run as a script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bcrypt  # noqa: E402
import yaml  # noqa: E402

from core import credential_backup  # noqa: E402

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "changeme"
CONFIG_PATH = ROOT / "config.yaml"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing credentials even if a hash is set.")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print(f"[bootstrap] ERROR: {CONFIG_PATH} does not exist.", file=sys.stderr)
        return 1

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    auth = raw.setdefault("auth", {})
    username = auth.get("default_username") or DEFAULT_USERNAME
    has_hash = bool((auth.get("default_password_hash") or "").strip())

    if has_hash and not args.force:
        print(f"[bootstrap] default_password_hash already set for user '{username}' — skipping (use --force to overwrite).")
        # Still ensure the backup file mirrors what we know publicly (the username).
        # We cannot recover an existing password, so backup is left as-is.
        return 0

    auth["default_username"] = DEFAULT_USERNAME
    auth["default_password_hash"] = _hash(DEFAULT_PASSWORD)

    CONFIG_PATH.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    credential_backup.write(DEFAULT_USERNAME, DEFAULT_PASSWORD)

    print("[bootstrap] Default credentials initialized:")
    print(f"             username: {DEFAULT_USERNAME}")
    print(f"             password: {DEFAULT_PASSWORD}")
    print(f"[bootstrap] Backup saved at {ROOT / '.credentials_backup.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
