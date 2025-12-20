"""
Coachd Telnyx Bridge
Handles 3-way conference calls with real-time audio streaming
Drop-in replacement for twilio_bridge.py
"""

import logging
import telnyx
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# Initialize Telnyx (only if configured)
if settings.telnyx_api_key:
    try:
        telnyx.api_key = settings.telnyx_api_key
        logger.info("Telnyx client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Telnyx client: {e}")


def is_telnyx_configured() -> bool:
    """Check if Telnyx is properly configured"""
    return bool(settings.telnyx_api_key and settings.telnyx_phone_number)


def initiate_agent_call(agent_phone: str, session_id: str) -> dict:
    """
    Step 1: Call the agent and put them in a conference room
    """
    if not is_telnyx_configured():
        return {"success": False, "error": "Telnyx not configured"}
    
    conference_name = f"coachd_{session_id}"
    
    try:
        call = telnyx.Call.create(
            connection_id=settings.telnyx_connection_id,
            to=agent_phone,
            from_=settings.telnyx_phone_number,
            webhook_url=f"{settings.base_url}/api/telnyx/agent-joined?session_id={session_id}",
            webhook_url_method="POST",
            answering_machine_detection="disabled",
            client_state=_encode_client_state({"session_id": session_id, "role": "agent"})
        )
        
        logger.info(f"Initiated agent call: {call.call_control_id} for session {session_id}")
        
        return {
            "success": True,
            "call_control_id": call.call_control_id,
            "call_leg_id": call.call_leg_id,
            "conference_name": conference_name,
            "session_id": session_id
        }
        
    except Exception as e:
        logger.error(f"Failed to initiate agent call: {e}")
        return {"success": False, "error": str(e)}


def generate_agent_conference_texml(session_id: str) -> str:
    """
    Generate TeXML to put the agent into the conference with recording.
    TeXML is TwiML-compatible.
    """
    conference_name = f"coachd_{session_id}"
    
    texml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Connected to Coachd. Dial your client now.</Say>
    <Dial>
        <Conference 
            startConferenceOnEnter="true"
            endConferenceOnExit="false"
            record="record-from-start"
            recordingStatusCallback="{settings.base_url}/api/telnyx/recording-complete?session_id={session_id}"
            statusCallback="{settings.base_url}/api/telnyx/conference-status?session_id={session_id}"
            statusCallbackEvent="start end join leave">
            {conference_name}
        </Conference>
    </Dial>
</Response>"""
    
    return texml


def add_client_to_conference(client_phone: str, session_id: str, agent_caller_id: str = None) -> dict:
    """
    Step 2: Add the client to the existing conference
    """
    if not is_telnyx_configured():
        return {"success": False, "error": "Telnyx not configured"}
    
    from_number = agent_caller_id if agent_caller_id else settings.telnyx_phone_number
    
    try:
        call = telnyx.Call.create(
            connection_id=settings.telnyx_connection_id,
            to=client_phone,
            from_=from_number,
            webhook_url=f"{settings.base_url}/api/telnyx/client-joined?session_id={session_id}",
            webhook_url_method="POST",
            answering_machine_detection="disabled",
            client_state=_encode_client_state({"session_id": session_id, "role": "client"})
        )
        
        logger.info(f"Added client to conference: {call.call_control_id}")
        
        return {
            "success": True,
            "call_control_id": call.call_control_id,
            "call_leg_id": call.call_leg_id,
            "client_phone": client_phone
        }
        
    except Exception as e:
        logger.error(f"Failed to add client to conference: {e}")
        return {"success": False, "error": str(e)}


def generate_client_conference_texml(session_id: str) -> str:
    """
    Generate TeXML to put the client into the conference
    """
    conference_name = f"coachd_{session_id}"
    
    texml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial>
        <Conference
            startConferenceOnEnter="false"
            endConferenceOnExit="true">
            {conference_name}
        </Conference>
    </Dial>
</Response>"""
    
    return texml


def join_conference(call_control_id: str, session_id: str) -> dict:
    """
    Join an answered call to the conference using Call Control API
    """
    if not is_telnyx_configured():
        return {"success": False, "error": "Telnyx not configured"}
    
    conference_name = f"coachd_{session_id}"
    
    try:
        call = telnyx.Call()
        call.call_control_id = call_control_id
        
        call.join_conference(
            conference_id=conference_name,
            start_conference_on_enter=True,
            hold=False,
            mute=False,
            supervisor_role="none"
        )
        
        logger.info(f"Joined call {call_control_id} to conference {conference_name}")
        return {"success": True, "conference_name": conference_name}
        
    except Exception as e:
        logger.error(f"Failed to join conference: {e}")
        return {"success": False, "error": str(e)}


def end_conference(session_id: str) -> dict:
    """
    End the conference and all associated calls
    """
    if not is_telnyx_configured():
        return {"success": False, "error": "Telnyx not configured"}
    
    conference_name = f"coachd_{session_id}"
    
    try:
        # List conferences and find ours
        conferences = telnyx.Conference.list()
        
        for conf in conferences.data:
            if conf.name == conference_name:
                # Get all participants and hang up
                participants = telnyx.ConferenceParticipant.list(conference_id=conf.id)
                for participant in participants.data:
                    try:
                        call = telnyx.Call()
                        call.call_control_id = participant.call_control_id
                        call.hangup()
                    except Exception as e:
                        logger.warning(f"Failed to hangup participant: {e}")
                
                # End the conference
                try:
                    conf.end()
                except Exception as e:
                    logger.warning(f"Failed to end conference: {e}")
        
        logger.info(f"Ended conference: {conference_name}")
        return {"success": True, "conference_name": conference_name}
        
    except Exception as e:
        logger.error(f"Failed to end conference: {e}")
        return {"success": False, "error": str(e)}


def hangup_call(call_control_id: str) -> dict:
    """
    Hang up a specific call
    """
    if not is_telnyx_configured():
        return {"success": False, "error": "Telnyx not configured"}
    
    try:
        call = telnyx.Call()
        call.call_control_id = call_control_id
        call.hangup()
        
        logger.info(f"Hung up call: {call_control_id}")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Failed to hangup call: {e}")
        return {"success": False, "error": str(e)}


def get_recording_url(recording_id: str) -> Optional[str]:
    """
    Get the URL for a completed recording
    """
    if not is_telnyx_configured():
        return None
    
    try:
        recording = telnyx.Recording.retrieve(recording_id)
        return recording.download_urls.get("mp3") or recording.download_urls.get("wav")
    except Exception as e:
        logger.error(f"Failed to get recording URL: {e}")
        return None


def _encode_client_state(data: dict) -> str:
    """Encode client state as base64 for Telnyx webhooks"""
    import base64
    import json
    return base64.b64encode(json.dumps(data).encode()).decode()


def _decode_client_state(encoded: str) -> dict:
    """Decode client state from Telnyx webhook"""
    import base64
    import json
    try:
        return json.loads(base64.b64decode(encoded).decode())
    except Exception:
        return {}
