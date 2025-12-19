"""
Coachd Twilio Bridge
Handles 3-way conference calls with real-time audio streaming
"""

import logging
from typing import Optional

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Dial, Conference

from .config import settings

logger = logging.getLogger(__name__)

# Initialize Twilio client (only if configured)
client = None
if settings.twilio_account_sid and settings.twilio_auth_token:
    try:
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        logger.info("Twilio client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Twilio client: {e}")


def is_twilio_configured() -> bool:
    """Check if Twilio is properly configured"""
    return client is not None and bool(settings.twilio_phone_number)


def initiate_agent_call(agent_phone: str, session_id: str) -> dict:
    """
    Step 1: Call the agent and put them in a conference room
    """
    if not client:
        return {"success": False, "error": "Twilio not configured"}
    
    conference_name = f"coachd_{session_id}"
    
    try:
        call = client.calls.create(
            to=agent_phone,
            from_=settings.twilio_phone_number,
            url=f"{settings.base_url}/api/twilio/agent-joined?session_id={session_id}",
            status_callback=f"{settings.base_url}/api/twilio/call-status?session_id={session_id}",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            record=False,  # We record the conference instead
        )
        
        logger.info(f"Initiated agent call: {call.sid} for session {session_id}")
        
        return {
            "success": True,
            "call_sid": call.sid,
            "conference_name": conference_name,
            "session_id": session_id
        }
        
    except Exception as e:
        logger.error(f"Failed to initiate agent call: {e}")
        return {"success": False, "error": str(e)}


def generate_agent_conference_twiml(session_id: str) -> str:
    """
    Generate TwiML to put the agent into the conference with recording
    """
    conference_name = f"coachd_{session_id}"
    
    response = VoiceResponse()
    response.say("Connected to Coachd. Dial your client now.", voice="alice")
    
    dial = Dial()
    
    conference = Conference(
        conference_name,
        start_conference_on_enter=True,
        end_conference_on_exit=False,
        record="record-from-start",
        recording_status_callback=f"{settings.base_url}/api/twilio/recording-complete?session_id={session_id}",
        status_callback=f"{settings.base_url}/api/twilio/conference-status?session_id={session_id}",
        status_callback_event="start end join leave",
    )
    
    dial.append(conference)
    response.append(dial)
    
    return str(response)


def add_client_to_conference(client_phone: str, session_id: str, agent_caller_id: str = None) -> dict:
    """
    Step 2: Add the client to the existing conference
    """
    if not client:
        return {"success": False, "error": "Twilio not configured"}
    
    from_number = agent_caller_id if agent_caller_id else settings.twilio_phone_number
    
    try:
        call = client.calls.create(
            to=client_phone,
            from_=from_number,
            url=f"{settings.base_url}/api/twilio/client-joined?session_id={session_id}",
            status_callback=f"{settings.base_url}/api/twilio/call-status?session_id={session_id}&party=client",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        
        logger.info(f"Added client to conference: {call.sid}")
        
        return {
            "success": True,
            "call_sid": call.sid,
            "client_phone": client_phone
        }
        
    except Exception as e:
        logger.error(f"Failed to add client to conference: {e}")
        return {"success": False, "error": str(e)}


def generate_client_conference_twiml(session_id: str) -> str:
    """
    Generate TwiML to put the client into the conference
    """
    conference_name = f"coachd_{session_id}"
    
    response = VoiceResponse()
    
    dial = Dial()
    conference = Conference(
        conference_name,
        start_conference_on_enter=False,
        end_conference_on_exit=True,
    )
    dial.append(conference)
    response.append(dial)
    
    return str(response)


def end_conference(session_id: str) -> dict:
    """
    End the conference and all associated calls
    """
    if not client:
        return {"success": False, "error": "Twilio not configured"}
    
    conference_name = f"coachd_{session_id}"
    
    try:
        conferences = client.conferences.list(friendly_name=conference_name, status="in-progress")
        
        for conf in conferences:
            participants = client.conferences(conf.sid).participants.list()
            for participant in participants:
                participant.delete()
            
            client.conferences(conf.sid).update(status="completed")
        
        logger.info(f"Ended conference: {conference_name}")
        return {"success": True, "conference_name": conference_name}
        
    except Exception as e:
        logger.error(f"Failed to end conference: {e}")
        return {"success": False, "error": str(e)}


def get_recording_url(recording_sid: str) -> Optional[str]:
    """
    Get the URL for a completed recording
    """
    if not client or not settings.twilio_account_sid:
        return None
    
    try:
        return f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Recordings/{recording_sid}.mp3"
    except Exception as e:
        logger.error(f"Failed to get recording URL: {e}")
        return None
