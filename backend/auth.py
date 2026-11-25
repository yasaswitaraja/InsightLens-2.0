
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


router = APIRouter(prefix="/api/auth", tags=["Authentication"])

password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


# Request and response schemas

class AuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    user_id: str
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# JWT helpers

def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")

    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is missing from backend/.env"
        )

    return secret


def create_access_token(user_id: uuid.UUID) -> str:
    expires_minutes = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    )

    if expires_minutes < 1:
        raise RuntimeError(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be positive"
        )

    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
    }

    return jwt.encode(
        payload,
        get_jwt_secret(),
        algorithm=ALGORITHM,
    )


def build_token_response(user: User) -> dict:
    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
        "user": {
            "user_id": str(user.id),
            "email": user.email,
        },
    }


# Password policy for new registrations only

COMMON_PASSWORDS = {
    "password123!", "password1234!", "password@123",
    "welcome123!", "admin123456!", "qwerty123456!",
    "iloveyou123!", "abc123456789!", "123456789abc!",
    "password@1234", "welcome@1234",
}


def validate_registration_password(password: str, email: str):
    errors = []

    if len(password) < 12:
        errors.append("at least 12 characters")

    if not re.search(r"[A-Z]", password):
        errors.append("at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        errors.append("at least one lowercase letter")

    if not re.search(r"[0-9]", password):
        errors.append("at least one number")

    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("at least one special character")

    lowered = password.lower()

    if lowered in COMMON_PASSWORDS:
        errors.append("not a commonly used password")

    email_name = email.split("@", 1)[0].lower()

    if len(email_name) >= 4 and email_name in lowered:
        errors.append("must not contain your email username")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must contain " + ", ".join(errors) + ".",
        )


# Registration

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    data: AuthRequest,
    db: Session = Depends(get_db),
):
    email = str(data.email).strip().lower()

    validate_registration_password(data.password, email)

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=email,
        password_hash=password_hash.hash(data.password),
    )

    db.add(user)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return build_token_response(user)


# Login

@router.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    data: AuthRequest,
    db: Session = Depends(get_db),
):
    email = str(data.email).strip().lower()

    user = db.scalar(
        select(User).where(User.email == email)
    )

    if not user or not password_hash.verify(
        data.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive.",
        )

    return build_token_response(user)


# Current authenticated user

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
) -> User:

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            get_jwt_secret(),
            algorithms=[ALGORITHM],
        )

        user_id = payload.get("sub")

        if not user_id or payload.get("type") != "access":
            raise unauthorized

        user_uuid = uuid.UUID(user_id)

    except (InvalidTokenError, ValueError, RuntimeError):
        raise unauthorized

    user = db.get(User, user_uuid)

    if not user or not user.is_active:
        raise unauthorized

    return user


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(
    current_user: User = Depends(get_current_user),
):
    return {
        "user_id": str(current_user.id),
        "email": current_user.email,
    }
