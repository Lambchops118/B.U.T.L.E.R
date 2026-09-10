"""Bounded briefing receipts and explicit feedback. No trigger endpoint."""

from fastapi import APIRouter, Depends, Query, Request

from talos.awareness.api.auth import require_write_auth
from talos.awareness.briefing.feedback import BriefingFeedback, record_feedback
from talos.awareness.briefing.service import BriefingStore

router = APIRouter()


@router.post("/briefings/feedback", dependencies=[Depends(require_write_auth)])
async def feedback(body: BriefingFeedback, request: Request) -> dict:
    return await record_feedback(request.app.state.engine, request.app.state.settings, body)


@router.get("/briefings", dependencies=[Depends(require_write_auth)])
async def recent(request: Request, limit: int = Query(default=10, ge=1, le=50)) -> dict:
    return {"deliveries": await BriefingStore(request.app.state.engine, request.app.state.settings).recent(limit)}
