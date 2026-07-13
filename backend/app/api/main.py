from fastapi import APIRouter
 
from app.api.routes import chat, conversations, login, private, users, utils, health, llm
from app.core.config import settings
 
api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(health.router)
api_router.include_router(llm.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
 
 
if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
