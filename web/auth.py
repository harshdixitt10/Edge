"""
JWT Authentication module for the Web UI.

Provides login/logout, token generation, role-based access control, and
request authentication middleware helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ── Roles ─────────────────────────────────────────────────────
# Hierarchy (admin can do everything operator can, etc.) is enforced
# explicitly at the route layer via require_role(*roles).
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
ALL_ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)


class AuthManager:
    """Simple JWT-based authentication."""

    def __init__(self, secret_key: str, algorithm: str = "HS256",
                 expiry_minutes: int = 60):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.expiry_minutes = expiry_minutes

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt."""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(password.encode(), password_hash.encode())
        except Exception:
            return False

    def create_token(self, username: str, role: str = ROLE_ADMIN) -> str:
        """Create a JWT access token carrying the user's role."""
        payload = {
            "sub": username,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=self.expiry_minutes),
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Optional[dict]:
        """Verify a JWT. Returns {username, role} if valid, None otherwise."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username = payload.get("sub")
            if not username:
                return None
            return {"username": username, "role": payload.get("role") or ROLE_ADMIN}
        except JWTError:
            return None


# ── Role-based access control helpers ────────────────────────

def get_current_user(request: Request) -> dict:
    """Pull the authenticated user from request.state (set by AuthMiddleware)."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_role(*allowed_roles: str):
    """FastAPI dependency that enforces the caller has one of `allowed_roles`.

    Usage:
        @router.post("/foo", dependencies=[Depends(require_role("admin"))])
    """
    allowed = set(allowed_roles) or set(ALL_ROLES)

    def _dep(request: Request) -> dict:
        user = get_current_user(request)
        if user["role"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {' or '.join(sorted(allowed))}",
            )
        return user

    return _dep


def has_role(user: Optional[dict], *allowed_roles: str) -> bool:
    """Plain helper for templates / non-route code."""
    if not user:
        return False
    return user.get("role") in set(allowed_roles)
