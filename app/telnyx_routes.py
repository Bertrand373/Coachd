"""
Coachd Telnyx Routes - Dual-Channel Click-to-Call
==================================================
POST /start-call → calls agent → agent answers → dials client → bridged
"""

import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .config import settings
from .telnyx_bridge import (
    is_telnyx_configured,
    normalize_phone,
    initiate_click_to_call,
    dial_client,
    generate_agent_answered_texml,
    generate_client_answered_texml,
    end_conference,
    hangup_call
)
from .call_session import session_manager, CallStatus
from .usage_tracker import log_telnyx_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telnyx", tags=["telnyx"])


class StartCallRequest(BaseModel):
    agent_phone: str
    client_phone: str  # Required for click-to-call

class EndCallRequest(BaseModel):
    session_id: str


def texml_response(content: str) -> Response:
    """Return TeXML with proper content type"""
    return Response(content=content, media_type="application/xml")


@router.post("/start-call")
async def start_call(data: StartCallRequest):
    """
    Start click-to-call session.
    Calls agent first, then client when agent answers.
    """
    if not is_telnyx_configured():
        raise HTTPException(status_code=503, detail="Telnyx not configured")
    
    agent_phone = normalize_phone(data.agent_phone)
    client_phone = normalize_phone(data.client_phone)
    
    if not client_phone:
        raise HTTPException(status_code=400, detail="Client phone number required")
    
    # Create session
    session = await session_manager.create_session(agent_phone)
    
    result = initiate_click_to_call(agent_phone, client_phone, session.session_id)
    
    if not result["success"]:
        await session_manager.update_session(session.session_id, status=CallStatus.FAILED)
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start call"))
    
    await session_manager.update_session(
        session.session_id,
        agent_call_sid=result["call_control_id"],
        client_phone=client_phone,
        status=CallStatus.AGENT_RINGING
    )
    
    return {
        "success": True,
        "session_id": session.session_id,
        "message": "Calling your phone now. Answer to connect with your client."
    }


@router.post("/end-call")
async def end_call(data: EndCallRequest):
    """End a call session"""
    session = await session_manager.get_session(data.session_id)
    
    if session and session.started_at:
        duration = session.get_duration() or 0
        log_telnyx_usage(
            call_duration_seconds=duration,
            agency_code=getattr(session, 'agency_code', None),
            session_id=data.session_id,
            call_control_id=session.agent_call_sid
        )
    
    end_conference(data.session_id)
    await session_manager.end_session(data.session_id)
    return {"success": True, "message": "Call ended"}


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get session details"""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.get("/status")
async def get_status():
    """Check if Telnyx is configured"""
    return {
        "configured": is_telnyx_configured(),
        "phone_number": settings.telnyx_phone_number if is_telnyx_configured() else None
    }


# ============ TELNYX WEBHOOKS ============

@router.post("/agent-answered")
async def agent_answered(request: Request):
    """
    Webhook: Agent answered. Now dial the client.
    """
    session_id = request.query_params.get("session_id", "unknown")
    client_phone = request.query_params.get("client_phone")
    agent_phone = request.query_params.get("agent_phone")
    
    logger.info(f"[Webhook] Agent answered: session={session_id}")
    print(f"[Webhook] Agent answered: session={session_id}, client={client_phone}", flush=True)
    
    await session_manager.update_session(
        session_id,
        status=CallStatus.AGENT_CONNECTED,
        started_at=datetime.utcnow()
    )
    
    # Dial the client
    if client_phone:
        result = dial_client(client_phone, agent_phone, session_id)
        
        if result["success"]:
            await session_manager.update_session(
                session_id,
                client_call_sid=result["call_control_id"],
                status=CallStatus.CLIENT_RINGING
            )
            await session_manager._broadcast_to_session(session_id, {
                "type": "client_dialing",
                "message": "Dialing client..."
            })
        else:
            logger.error(f"Failed to dial client: {result.get('error')}")
            await session_manager._broadcast_to_session(session_id, {
                "type": "error",
                "message": f"Failed to dial client: {result.get('error')}"
            })
    
    # Return TeXML to put agent in conference
    texml = generate_agent_answered_texml(session_id)
    return texml_response(texml)


@router.post("/client-answered")
async def client_answered(request: Request):
    """
    Webhook: Client answered. Bridge complete.
    """
    session_id = request.query_params.get("session_id", "unknown")
    
    logger.info(f"[Webhook] Client answered: session={session_id}")
    print(f"[Webhook] Client answered: session={session_id}", flush=True)
    
    await session_manager.update_session(session_id, status=CallStatus.IN_PROGRESS)
    
    # Notify frontend
    await session_manager._broadcast_to_session(session_id, {
        "type": "client_connected",
        "message": "Client connected - coaching active"
    })
    
    # Return TeXML to stream client audio and join conference
    texml = generate_client_answered_texml(session_id)
    return texml_response(texml)


@router.post("/call-status")
async def call_status(request: Request):
    """Webhook: Call status updates"""
    try:
        form = await request.form()
        status = form.get("CallStatus")
        session_id = request.query_params.get("session_id")
        
        logger.info(f"Call status: {status} for session {session_id}")
        
        if session_id and status == "completed":
            session = await session_manager.get_session(session_id)
            if session:
                await session_manager.end_session(session_id)
    except Exception as e:
        logger.error(f"Error handling call status: {e}")
    
    return Response(content="", status_code=200)


@router.post("/webhook")
async def main_webhook(request: Request):
    """Main webhook for Call Control API events"""
    try:
        body = await request.json()
        event_type = body.get("data", {}).get("event_type", "")
        logger.info(f"Webhook event: {event_type}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    
    return Response(content="", status_code=200)