from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from runsigil_control_api.auth import internal_action_session
from runsigil_control_api.schemas import InternalAuthorizationInput, InternalAuthorizationResponse
from runsigil_control_api.services.governed_actions import authorize_gateway_action

router = APIRouter(prefix="/internal/v1")


@router.post("/actions/{action_id}/authorize", response_model=InternalAuthorizationResponse)
def authorize_action(
    action_id: UUID,
    request: InternalAuthorizationInput,
    service_token: Annotated[str | None, Header(alias="X-RunSigil-Service-Token")] = None,
) -> InternalAuthorizationResponse:
    with internal_action_session(action_id, service_token) as session:
        return authorize_gateway_action(
            session,
            action_id=action_id,
            content_digest=request.content_digest,
            claim_token=request.claim_token,
            mode=request.mode,
        )
