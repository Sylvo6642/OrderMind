"""FastAPI app. Run with: uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import admin, auth, chat, orders
from app.db import ensure_indexes, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes(get_db())
    yield


app = FastAPI(title="OrderMind", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(chat.router)
app.include_router(admin.router)
