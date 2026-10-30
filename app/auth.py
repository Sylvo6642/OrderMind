"""Register, login and the current-user dependency.

get_current_user is the only place a user id enters the app: it comes from
a verified JWT, never from the request body or the model.
"""
import re

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.db import get_db
from app.security import create_access_token, decode_access_token, verify_password
from app.users import create_user

router = APIRouter(tags=["auth"])
bearer = HTTPBearer(auto_error=False)

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.fullmatch(value):
            raise ValueError("not a valid email address")
        return value

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: str
    email: str
    name: str
    is_admin: bool


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Database = Depends(get_db),
) -> dict:
    user_id = decode_access_token(credentials.credentials) if credentials else None
    user = None
    if user_id and ObjectId.is_valid(user_id):
        user = db.users.find_one({"_id": ObjectId(user_id)})
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
        )
    return user


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Database = Depends(get_db)) -> TokenOut:
    try:
        user = create_user(db, body.email, body.name, body.password)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return TokenOut(access_token=create_access_token(str(user["_id"])))


@router.post("/auth/login")
def login(body: LoginIn, db: Database = Depends(get_db)) -> TokenOut:
    user = db.users.find_one({"email": body.email.strip().lower()})
    if not verify_password(user["password_hash"] if user else None, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenOut(access_token=create_access_token(str(user["_id"])))


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> MeOut:
    return MeOut(id=str(user["_id"]), email=user["email"], name=user["name"], is_admin=user["is_admin"])
