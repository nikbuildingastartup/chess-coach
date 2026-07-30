import pytest
from fastapi import HTTPException

from app.auth import require_auth
from app.config import settings


def test_require_auth_valid_token_passes():
    require_auth(authorization=f"Bearer {settings.app_secret}")


def test_require_auth_missing_header_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization=None)
    assert exc_info.value.status_code == 401


def test_require_auth_wrong_token_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization="Bearer wrong-secret")
    assert exc_info.value.status_code == 401


def test_require_auth_wrong_scheme_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization=f"Basic {settings.app_secret}")
    assert exc_info.value.status_code == 401
