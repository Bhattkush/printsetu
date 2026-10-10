import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from threading import Lock
import requests
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_db
from .models import AuthSession, EmailChallenge, PricingRule, RateLimit, Shop, User

router = APIRouter(prefix="/api/auth")
passwords = PasswordHasher()
dummy_hash = passwords.hash('not-an-account-password')
rate_lock = Lock()
settings = get_settings()


def digest(value: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def throttle(db: Session, key: str, limit=10, seconds=600):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    key = digest(key + ':' + str(int(time.time()) // seconds))
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    statement = insert(RateLimit).values(id=key, count=1, window_start=datetime.utcnow())
    statement = statement.on_conflict_do_update(index_elements=['id'], set_={'count': RateLimit.count + 1}).returning(RateLimit.count)
    count = db.execute(statement).scalar_one()
    db.commit()
    if count > limit:
        raise HTTPException(429, "Too many attempts. Please try again later.")


def current_session(request: Request, db: Session) -> AuthSession:
    session = db.get(AuthSession, digest(request.cookies.get("ps_session", "")))
    if not session or session.expires_at < datetime.utcnow():
        raise HTTPException(401, "Please sign in")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(session.csrf, request.headers.get("x-csrf-token", "")):
        raise HTTPException(403, "Session security check failed. Refresh and try again.")
    return session


def owner(request: Request, db: Session = Depends(get_db)) -> User:
    user = db.get(User, current_session(request, db).user_id)
    if not user or not user.verified or user.role not in {"owner", "admin"}:
        raise HTTPException(403, "Verify your email to continue")
    return user


def admin(user: User = Depends(owner)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def require_shop(db: Session, user: User, shop_id: str) -> Shop:
    shop = db.get(Shop, shop_id)
    if not shop or (user.role != "admin" and shop.owner_id != user.id):
        raise HTTPException(404, "Shop not found")
    return shop


def customer(request: Request, response: Response) -> str:
    token = request.cookies.get("ps_customer")
    if not token or len(token) < 32:
        token = secrets.token_urlsafe(32)
        response.set_cookie("ps_customer", token, httponly=True, secure=settings.env == "production", samesite="lax", max_age=86400)
    return digest(token)


def issue_session(db, user, response):
    raw = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    db.add(AuthSession(id=digest(raw), user_id=user.id, csrf=csrf, expires_at=datetime.utcnow() + timedelta(hours=12)))
    db.commit()
    response.set_cookie("ps_session", raw, httponly=True, secure=settings.env == "production", samesite="lax", max_age=43200)
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "verified": user.verified}, "csrf": csrf}


def challenge(db, user, purpose):
    previous = db.query(EmailChallenge).filter_by(user_id=user.id, purpose=purpose).order_by(EmailChallenge.sent_at.desc()).first()
    if previous and (datetime.utcnow() - previous.sent_at).total_seconds() < 60:
        raise HTTPException(429, "Wait 60 seconds before requesting another code")
    code = f"{secrets.randbelow(1000000):06d}"
    db.add(EmailChallenge(user_id=user.id, purpose=purpose, code_hash=digest(user.id + purpose + code), expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    if settings.otp_mode == "development" and settings.env == "local":
        return {"message": "Development-only email verification", "development_code": code}
    if not settings.resend_api_key:
        raise HTTPException(503, "Email delivery has not been configured")
    try:
        result = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}"}, json={"from": settings.resend_sender, "to": [user.email], "subject": "Your PrintSetu verification code", "text": f"Your email verification code is {code}. It expires in 10 minutes. If you did not request this code, ignore this email."}, timeout=15)
        result.raise_for_status()
    except requests.RequestException:
        raise HTTPException(503, "Email could not be delivered. Try again later.")
    return {"message": "If eligible, an email verification code has been sent."}


def verify_challenge(db, user, purpose, code):
    row = db.query(EmailChallenge).filter_by(user_id=user.id, purpose=purpose).order_by(EmailChallenge.sent_at.desc()).with_for_update().first() if user else None
    if not row or row.used_at or row.expires_at < datetime.utcnow() or row.attempts >= 5:
        raise HTTPException(400, "Code is invalid or expired")
    row.attempts += 1
    valid = hmac.compare_digest(row.code_hash, digest(user.id + purpose + code))
    if valid:
        row.used_at = datetime.utcnow()
    db.commit()
    if not valid:
        raise HTTPException(400, "Code is invalid or expired")


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    shop_name: str = Field(min_length=1, max_length=100)


class EmailBody(BaseModel):
    email: EmailStr


class CodeBody(EmailBody):
    code: str = Field(pattern=r"^\d{6}$")


class ResetBody(CodeBody):
    password: str = Field(min_length=10, max_length=128)


@router.post("/register")
def register(body: Registration, request: Request, db: Session = Depends(get_db)):
    throttle(db, "register:" + request.client.host, 5)
    email = str(body.email).lower()
    existing = db.query(User).filter_by(email=email).first()
    if existing:
        return {"message": "If eligible, an email verification code has been sent."}
    user = User(name=body.name, email=email, password_hash=passwords.hash(body.password), role="owner")
    db.add(user); db.flush()
    shop = Shop(owner_id=user.id, name=body.shop_name, slug="shop-" + secrets.token_hex(5), demo_enabled=settings.unpaid_printing_allowed)
    db.add(shop); db.flush()
    db.add(PricingRule(shop_id=shop.id, bw_page_price=2, color_page_price=10))
    db.commit()
    return challenge(db, user, "verify")


@router.post("/resend")
def resend(body: EmailBody, request: Request, db: Session = Depends(get_db)):
    throttle(db, "resend:" + request.client.host, 5)
    user = db.query(User).filter_by(email=str(body.email).lower()).first()
    return challenge(db, user, "verify") if user and not user.verified else {"message": "If eligible, an email verification code has been sent."}


@router.post("/verify")
def verify(body: CodeBody, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(db, "verify:" + request.client.host, 15)
    user = db.query(User).filter_by(email=str(body.email).lower()).first()
    verify_challenge(db, user, "verify", body.code)
    user.verified = True
    return issue_session(db, user, response)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(db, "login:" + request.client.host, 10)
    throttle(db, "login-email:" + str(body.email).lower(), 10)
    user = db.query(User).filter_by(email=str(body.email).lower()).first()
    valid = False
    try:
        valid = passwords.verify(user.password_hash if user else dummy_hash, body.password)
    except (VerificationError, InvalidHashError):
        pass
    if not user or not valid:
        raise HTTPException(401, "Email or password is incorrect")
    if not user.verified:
        raise HTTPException(403, "Verify your email before signing in")
    return issue_session(db, user, response)


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    user = db.get(User, session.user_id)
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "verified": user.verified}, "csrf": session.csrf}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    db.delete(current_session(request, db)); db.commit()
    response.delete_cookie("ps_session")
    return {"ok": True}


@router.post("/forgot-password")
def forgot(body: EmailBody, request: Request, db: Session = Depends(get_db)):
    throttle(db, "reset:" + request.client.host, 5)
    user = db.query(User).filter_by(email=str(body.email).lower()).first()
    return challenge(db, user, "reset") if user else {"message": "If eligible, a password reset code has been sent."}


@router.post("/reset-password")
def reset(body: ResetBody, request: Request, db: Session = Depends(get_db)):
    throttle(db, "reset-verify:" + request.client.host, 15)
    user = db.query(User).filter_by(email=str(body.email).lower()).first()
    verify_challenge(db, user, "reset", body.code)
    user.password_hash = passwords.hash(body.password)
    db.query(AuthSession).filter_by(user_id=user.id).delete()
    db.commit()
    return {"message": "Password reset. Sign in with your new password."}
