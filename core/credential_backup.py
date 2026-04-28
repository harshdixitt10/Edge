"""
Local credential backup — break-glass recovery for the admin login.

Writes the current admin username and password (base64-encoded, NOT encrypted)
to a file next to config.yaml. If the admin forgets their credentials, they
can read this file directly on the device to recover them.

Security note: base64 is encoding, not encryption. This file MUST stay on the
device (gitignored, never uploaded, never included in adapter snapshots). It
exists purely so the operator with shell access to the box can recover their
own password.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BACKUP_FILENAME = ".credentials_backup.yaml"


def _backup_path() -> Path:
    return Path(__file__).resolve().parent.parent / BACKUP_FILENAME


def write(username: str, password: str) -> None:
    """Write the current admin credentials to the local backup file.

    Called whenever credentials change (first-boot bootstrap or Settings UI).
    `password` must be the plaintext — bcrypt hashes can't be reversed, so
    this is the only place we capture it.
    """
    path = _backup_path()
    u_b64 = base64.b64encode(username.encode("utf-8")).decode("ascii")
    p_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    ts = datetime.now(timezone.utc).isoformat()

    content = (
        "# Local credential backup — DO NOT SHARE\n"
        "# Restore by base64-decoding the values below and signing in normally.\n"
        "#   echo <username_b64> | base64 -d\n"
        "#   echo <password_b64> | base64 -d\n"
        f"username_b64: {u_b64}\n"
        f"password_b64: {p_b64}\n"
        f"updated_at: {ts}\n"
    )

    try:
        path.write_text(content, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        logger.info(f"Credential backup written to {path.name}")
    except OSError as e:
        logger.warning(f"Failed to write credential backup: {e}")


def read() -> Optional[dict]:
    """Read the backup file. Returns dict with username/password or None."""
    path = _backup_path()
    if not path.exists():
        return None
    try:
        u_b64 = p_b64 = None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("username_b64:"):
                u_b64 = line.split(":", 1)[1].strip()
            elif line.startswith("password_b64:"):
                p_b64 = line.split(":", 1)[1].strip()
        if not u_b64 or not p_b64:
            return None
        return {
            "username": base64.b64decode(u_b64).decode("utf-8"),
            "password": base64.b64decode(p_b64).decode("utf-8"),
        }
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read credential backup: {e}")
        return None
