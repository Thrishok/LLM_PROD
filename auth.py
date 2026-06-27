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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
 
from db import create_email_user, get_user_by_email, upsert_google_user
 
router = APIRouter()
 
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
 
 
def _redirect_uri(request: Request) -> str:
    """Where Google sends the user back to.
 
    Behind Render's proxy the request scheme can look like http, so we allow
    an explicit override via OAUTH_REDIRECT_URI (set this in production).
    """
    return os.getenv("OAUTH_REDIRECT_URI") or str(request.url_for("auth_callback"))
 
 
@router.get("/auth/login")
async def auth_login(request: Request):
    return await oauth.google.authorize_redirect(request, _redirect_uri(request))
 
 
@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        # User denied consent, or the state/nonce didn't validate.
        return RedirectResponse(url="/login?error=denied")
 
    info = token.get("userinfo")
    if not info or not info.get("email_verified", False):
        return RedirectResponse(url="/login?error=unverified")
 
    user = upsert_google_user(
        google_sub=info["sub"],
        email=info["email"],
        name=info.get("name", ""),
        picture=info.get("picture", ""),
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
    request.session["user"] = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
    }
 
 
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
 
