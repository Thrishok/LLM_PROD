"""Google OAuth login + session helpers for the FastAPI app.

Flow:
  /auth/login    -> redirect the browser to Google's consent screen
  /auth/callback -> Google redirects back here; we verify, store the user,
                    and put their profile in a signed session cookie
  /auth/logout   -> clear the session

The signed session cookie is provided by Starlette's SessionMiddleware
(see main.py). We never store passwords — Google handles authentication.
"""

import os
import re

import bcrypt
from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from db import create_email_user, get_user_by_email, upsert_google_user

load_dotenv()

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _validate_google_settings() -> None:
    if not oauth.google.client_id or not oauth.google.client_secret:
        raise RuntimeError(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )


def _redirect_uri(request: Request) -> str:
    override = os.getenv("OAUTH_REDIRECT_URI")
    if override:
        return override

    callback_url = str(request.url_for("auth_callback"))
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return re.sub(r"^https?://[^/]+", f"{scheme}://{host}", callback_url)

    return callback_url


async def _get_google_profile(request: Request) -> dict:
    _validate_google_settings()
    token = await oauth.google.authorize_access_token(request)
    if not token:
        raise ValueError("No token returned from Google.")

    try:
        claims = await oauth.google.parse_id_token(request, token)
    except Exception as exc:
        raise ValueError("Unable to verify Google ID token.") from exc

    if not claims.get("email_verified"):
        raise ValueError("Google email is not verified.")

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise ValueError("Incomplete Google profile returned from Google.")

    return {
        "google_sub": sub,
        "email": email.strip().lower(),
        "name": claims.get("name", "") or "",
        "picture": claims.get("picture", "") or "",
    }


@router.get("/auth/login")
async def auth_login(request: Request):
    try:
        _validate_google_settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return await oauth.google.authorize_redirect(request, _redirect_uri(request))


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        profile = await _get_google_profile(request)
    except OAuthError:
        return RedirectResponse(url="/login?error=denied")
    except ValueError:
        return RedirectResponse(url="/login?error=invalid")
    except Exception:
        return RedirectResponse(url="/login?error=server")

    user = upsert_google_user(
        google_sub=profile["google_sub"],
        email=profile["email"],
        name=profile["name"],
        picture=profile["picture"],
    )

    _set_session(request, user)
    return RedirectResponse(url="/")


@router.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/login")


# --------------------------------------------------------------------------- #
# Email + password auth
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; cap to avoid an error on long inputs.
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except ValueError:
        return False


def _set_session(request: Request, user) -> None:
    """Store the minimal, non-sensitive profile in the signed cookie."""
    session_user = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
    }
    if getattr(user, "google_sub", None):
        session_user["sub"] = user.google_sub
    request.session["user"] = session_user


class RegisterRequest(BaseModel):
    name: str = Field("", max_length=80)
    email: str
    password: str = Field(..., min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
def auth_register(req: RegisterRequest, request: Request):
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    existing = get_user_by_email(email)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="An account with that email already exists."
        )

    user = create_email_user(
        email=email,
        password_hash=_hash_password(req.password),
        name=req.name.strip(),
    )
    _set_session(request, user)
    return {"ok": True}


@router.post("/auth/login-password")
def auth_login_password(req: LoginRequest, request: Request):
    email = req.email.strip().lower()
    user = get_user_by_email(email)

    # Generic message so we don't reveal which emails are registered.
    if user is None or not user.password_hash or not _verify_password(
        req.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    _set_session(request, user)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def current_user(request: Request) -> dict:
    """Dependency for API routes: 401 if the caller isn't logged in."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


def optional_user(request: Request) -> dict | None:
    """Dependency for pages: returns the user or None (no error)."""
    return request.session.get("user")
