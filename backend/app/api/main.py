from fastapi import APIRouter

from app.api.routes import chat, conversations, health, utils

api_router = APIRouter()
api_router.include_router(utils.router)
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
