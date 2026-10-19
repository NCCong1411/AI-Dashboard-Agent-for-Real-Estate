"""Builder-only conversation creation, retrieval and draft generation."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth.dependencies import Builder, Database, require_trusted_origin
from app.schemas.agent import AgentResponse, ApprovalRequest, ApprovalResponse, ConversationView, CreateConversationRequest, MessageRequest
from app.services.approval import ApprovalService
from app.services.conversation import ConversationService, get_conversation_service


router = APIRouter(prefix="/api/conversations", tags=["conversations"])
Service = Annotated[ConversationService, Depends(get_conversation_service)]


@router.post("", response_model=ConversationView, dependencies=[Depends(require_trusted_origin)], status_code=201)
async def create_conversation(request: CreateConversationRequest, user: Builder, database: Database, service: Service):
    user_id = user.id
    database.rollback()  # release the authentication transaction before work
    return await asyncio.to_thread(service.create, user_id, request.dashboard_id)


@router.get("/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: UUID, user: Builder, database: Database, service: Service):
    user_id = user.id
    database.rollback()
    return await asyncio.to_thread(service.get, user_id, conversation_id)


@router.post("/{conversation_id}/messages", response_model=AgentResponse,
             dependencies=[Depends(require_trusted_origin)])
async def post_message(conversation_id: UUID, request: MessageRequest, user: Builder, database: Database, service: Service):
    user_id = user.id
    database.rollback()
    return await service.send(user_id, conversation_id, request)


@router.post("/{conversation_id}/approval", response_model=ApprovalResponse,
             dependencies=[Depends(require_trusted_origin)])
async def approve_draft(conversation_id: UUID, request: ApprovalRequest, user: Builder, database: Database, service: Service):
    user_id = user.id
    database.rollback()
    return await ApprovalService(service).decide(user_id, conversation_id, request)
