"""
JWT Authentication module for the Web UI.

Provides login/logout, token generation, and request authentication middleware.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

logger = logging.getLogger(__name__)


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

    def create_token(self, username: str) -> str:
        """Create a JWT access token."""
        payload = {
            "sub": username,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=self.expiry_minutes),
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Optional[str]:
        """Verify a JWT token. Returns username if valid, None otherwise."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username: str = payload.get("sub")
            return username
        except JWTError:
            return None
