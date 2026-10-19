"""Test-only ASGI factory for provider failure; never a runtime API feature."""

import os

from app.config import get_settings


def create_app():
    settings = get_settings()
    if settings.app_env != "test" or settings.llm_mode != "mock" or os.getenv("E2E_PROVIDER_FAILURE") != "true":
        raise RuntimeError("browser fault factory requires explicit test/mock configuration")
    from app.main import app
    from app.services.conversation import get_conversation_service
    class UnavailablePlanner:
        async def generate(self, *args, **kwargs):
            raise RuntimeError("private-provider-failure-canary")
    get_conversation_service().nodes.planner = UnavailablePlanner()
    return app
