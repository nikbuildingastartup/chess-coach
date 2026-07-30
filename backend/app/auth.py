from fastapi import Header, HTTPException, status

from app.config import settings


def require_auth(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: require `Authorization: Bearer <app_secret>`.

    Raises 401 if the header is missing or the token doesn't match.
    """
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.app_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
