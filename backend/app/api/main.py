from fastapi import APIRouter

from app.api.routes import (
    blog,
    chat,
    conversations,
    discovery,
    guides,
    health,
    price_subscriptions,
    utils,
)

api_router = APIRouter()
api_router.include_router(utils.router)
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(discovery.router)
api_router.include_router(blog.router)
api_router.include_router(guides.router)
api_router.include_router(price_subscriptions.router)
