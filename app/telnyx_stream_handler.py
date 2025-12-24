"""
Coachd Telnyx Stream Handler - Dual-Channel
============================================
- Client stream: 100% client audio → Deepgram → guidance triggers
- Agent stream: 100% agent audio → recording only (no Deepgram)

No diarization needed = 100% speaker accuracy
"""

import asyncio
import base64
import time
import audioop
from typing import Dict

from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions

from .config import settings
from .rag_engine import get_rag_engine, CallContext
from .call_state_machine import CallStateMachine
from .call_session import session_manager
from .usage_tracker import log_deepgram_usage

import logging
logger = logging.getLogger(__name__)


class ClientStreamHandler:
    """
    Handles CLIENT audio only.
    All audio → Deepgram → objection detection → guidance.
    """
    
    SAMPLE_RATE = 8000
    GUIDANCE_COOLDOWN_SECONDS = 3.0
    MIN_WORDS_FOR_GUIDANCE = 8
    
    HOT_TRIGGERS = [
        "can't afford", "too expensive", "not interested", "no money",
        "think about it", "talk to my", "spouse", "wife", "husband",
        "call back", "busy", "not a good time", "don't need",
        "already have", "too much", "let me think", "send information",
        "how much", "what's the cost", "what's the price",
        "i need to", "let me", "give me some time", "not right now",
        "i'm not sure", "that's a lot", "can't do that", "don't have"
    ]
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.deepgram = None
        self.connection = None
        self.is_running = False
        
        self._client_buffer = ""
        self._full_transcript = []
        
        self._last_guidance_time = 0
        self._generating_guidance = False
        self._rag_engine = None
        self._state_machine = None
        
        self._total_audio_bytes = 0
        self._session_start_time = None
        self._agency = None
        
        logger.info(f"[ClientStream] Created for session {session_id}")
    
    async def start(self) -> bool:
        """Initialize Deepgram for client audio"""
        logger.info(f"[ClientStream] Starting for {self.session_id}")
        print(f"[ClientStream] Starting for {self.session_id}", flush=True)
        
        self._session_start_time = time.time()
        
        session = await session_manager.get_session(self.session_id)
        if session:
            self._agency = getattr(session, 'agency', None)
        
        try:
            self._rag_engine = get_rag_engine()
        except Exception as e:
            logger.warning(f"[ClientStream] RAG unavailable: {e}")
        
        try:
            self._state_machine = CallStateMachine(session_id=self.session_id)
        except Exception as e:
            logger.warning(f"[ClientStream] State machine unavailable: {e}")
        
        if not settings.deepgram_api_key:
            logger.warning("[ClientStream] No Deepgram key")
            self.is_running = True
            await self._broadcast({"type": "ready", "message": "Connected (no transcription)"})
            return True
        
        try:
            self.deepgram = DeepgramClient(settings.deepgram_api_key)
            self.connection = self.deepgram.listen.asynclive.v("1")
            
            self.connection.on(LiveTranscriptionEvents.Open, self._on_open)
            self.connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
            self.connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self.connection.on(LiveTranscriptionEvents.Close, self._on_close)
            
            # NO DIARIZATION - all audio is client
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                smart_format=True,
                punctuate=True,
                interim_results=True,
                utterance_end_ms=1000,
                encoding="linear16",
                sample_rate=self.SAMPLE_RATE,
                channels=1
            )
            
            await self.connection.start(options)
            self.is_running = True
            
            print(f"[ClientStream] Deepgram connected (client-only)", flush=True)
            
            await self._broadcast({
                "type": "ready",
                "message": "Live coaching active"
            })
            
            # Immediately notify that speakers are identified (they're always known in dual-channel)
            await self._broadcast({
                "type": "speakers_identified",
                "message": "Client connected - coaching active"
            })
            
            return True
            
        except Exception as e:
            logger.error(f"[ClientStream] Deepgram error: {e}")
            self.is_running = True
            await self._broadcast({"type": "ready", "message": "Connected (transcription unavailable)"})
            return True
    
    async def handle_telnyx_message(self, message: dict):
        """Process Telnyx message with client audio"""
        event = message.get("event")
        
        if event == "connected":
            print(f"[ClientStream] Telnyx connected", flush=True)
            
        elif event == "start":
            print(f"[ClientStream] Stream started", flush=True)
            
        elif event == "media":
            media = message.get("media", {})
            payload = media.get("payload")
            
            if payload:
                try:
                    ulaw_audio = base64.b64decode(payload)
                    self._total_audio_bytes += len(ulaw_audio)
                    pcm_audio = audioop.ulaw2lin(ulaw_audio, 2)
                    
                    if self.connection and self.is_running:
                        await self.connection.send(pcm_audio)
                except Exception as e:
                    logger.error(f"[ClientStream] Audio error: {e}")
                    
        elif event == "stop":
            print(f"[ClientStream] Stream stopped", flush=True)
            await self.stop()
    
    async def _on_open(self, *args, **kwargs):
        print(f"[ClientStream] Deepgram open", flush=True)
    
    async def _on_transcript(self, *args, **kwargs):
        """Handle transcript - ALL is client speech"""
        try:
            result = kwargs.get('result') or (args[1] if len(args) > 1 else None)
            if not result:
                return
            
            alternatives = result.channel.alternatives
            if not alternatives:
                return
            
            transcript = alternatives[0].transcript
            is_final = result.is_final
            
            if not transcript:
                return
            
            print(f"[ClientStream] CLIENT ({'FINAL' if is_final else 'interim'}): {transcript[:60]}", flush=True)
            
            # Broadcast to frontend - always "caller" (client)
            await self._broadcast({
                "type": "transcript",
                "text": transcript,
                "speaker": "caller",
                "is_final": is_final,
                "roles_locked": True
            })
            
            if is_final:
                self._client_buffer += " " + transcript
                self._full_transcript.append({
                    "speaker": "client",
                    "text": transcript,
                    "timestamp": time.time()
                })
                
                if self._state_machine:
                    self._state_machine.add_transcript(transcript, is_final=True)
                
                await self._check_guidance_trigger(transcript)
                
        except Exception as e:
            logger.error(f"[ClientStream] Transcript error: {e}")
    
    async def _check_guidance_trigger(self, transcript: str):
        """Check if client speech should trigger guidance"""
        if self._generating_guidance:
            return
        
        now = time.time()
        if now - self._last_guidance_time < self.GUIDANCE_COOLDOWN_SECONDS:
            return
        
        transcript_lower = transcript.lower()
        triggered = False
        trigger_phrase = None
        
        for trigger in self.HOT_TRIGGERS:
            if trigger in transcript_lower:
                triggered = True
                trigger_phrase = trigger
                break
        
        word_count = len(transcript.split())
        if not triggered and word_count >= self.MIN_WORDS_FOR_GUIDANCE:
            triggered = True
            trigger_phrase = f"{word_count} words"
        
        if triggered:
            print(f"[ClientStream] 🎯 TRIGGER: '{trigger_phrase}'", flush=True)
            await self._generate_guidance(transcript)
    
    async def _generate_guidance(self, trigger_text: str):
        """Generate AI guidance"""
        if not self._rag_engine:
            return
        
        self._generating_guidance = True
        self._last_guidance_time = time.time()
        
        try:
            context = CallContext(
                call_type="presentation",
                product="life insurance",
                recent_transcript=self._client_buffer[-500:],
                client_profile={}
            )
            
            await self._broadcast({
                "type": "guidance_start",
                "trigger": trigger_text[:50]
            })
            
            guidance_text = ""
            async for chunk in self._rag_engine.get_guidance_stream(
                trigger_text,
                context,
                agency=self._agency
            ):
                guidance_text += chunk
                await self._broadcast({
                    "type": "guidance_chunk",
                    "text": chunk
                })
            
            await self._broadcast({
                "type": "guidance_complete",
                "full_text": guidance_text
            })
            
            await session_manager.add_guidance(self.session_id, {
                "trigger": trigger_text[:50],
                "response": guidance_text
            })
            
        except Exception as e:
            logger.error(f"[ClientStream] Guidance error: {e}")
        finally:
            self._generating_guidance = False
    
    async def _broadcast(self, message: dict):
        """Send to frontend"""
        await session_manager._broadcast_to_session(self.session_id, message)
    
    async def _on_error(self, *args, **kwargs):
        error = kwargs.get('error') or (args[1] if len(args) > 1 else "Unknown")
        logger.error(f"[ClientStream] Deepgram error: {error}")
    
    async def _on_close(self, *args, **kwargs):
        logger.info(f"[ClientStream] Deepgram closed")
    
    async def stop(self):
        """Stop and log usage"""
        print(f"[ClientStream] Stopping {self.session_id}", flush=True)
        self.is_running = False
        
        if self._total_audio_bytes > 0 and self._session_start_time:
            bytes_per_second = self.SAMPLE_RATE * 2
            duration_seconds = self._total_audio_bytes / bytes_per_second
            cost = log_deepgram_usage(
                duration_seconds=duration_seconds,
                agency_code=self._agency,
                session_id=self.session_id,
                model='nova-2'
            )
            print(f"[ClientStream] Usage: {duration_seconds:.1f}s = ${cost:.4f}", flush=True)
        
        if self.connection:
            try:
                await asyncio.wait_for(self.connection.finish(), timeout=3.0)
            except:
                pass
        
        await self._broadcast({"type": "stream_ended"})


class AgentStreamHandler:
    """
    Handles AGENT audio - recording only, NO Deepgram.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.is_running = False
        self._total_audio_bytes = 0
        logger.info(f"[AgentStream] Created for session {session_id}")
    
    async def start(self) -> bool:
        print(f"[AgentStream] Starting for {self.session_id} (recording only)", flush=True)
        self.is_running = True
        return True
    
    async def handle_telnyx_message(self, message: dict):
        """Handle agent audio - recording only"""
        event = message.get("event")
        
        if event == "connected":
            print(f"[AgentStream] Connected", flush=True)
        elif event == "media":
            media = message.get("media", {})
            payload = media.get("payload")
            if payload:
                self._total_audio_bytes += len(base64.b64decode(payload))
        elif event == "stop":
            await self.stop()
    
    async def stop(self):
        print(f"[AgentStream] Stopped - {self._total_audio_bytes} bytes", flush=True)
        self.is_running = False


# Handler management
_client_handlers: Dict[str, ClientStreamHandler] = {}
_agent_handlers: Dict[str, AgentStreamHandler] = {}


async def get_or_create_client_handler(session_id: str) -> ClientStreamHandler:
    """Get or create client stream handler"""
    if session_id not in _client_handlers:
        handler = ClientStreamHandler(session_id)
        if await handler.start():
            _client_handlers[session_id] = handler
        else:
            raise Exception("Failed to start client handler")
    return _client_handlers[session_id]


async def get_or_create_agent_handler(session_id: str) -> AgentStreamHandler:
    """Get or create agent stream handler"""
    if session_id not in _agent_handlers:
        handler = AgentStreamHandler(session_id)
        if await handler.start():
            _agent_handlers[session_id] = handler
        else:
            raise Exception("Failed to start agent handler")
    return _agent_handlers[session_id]


async def remove_handler(session_id: str):
    """Remove handlers for session"""
    if session_id in _client_handlers:
        handler = _client_handlers.pop(session_id)
        await handler.stop()
    if session_id in _agent_handlers:
        handler = _agent_handlers.pop(session_id)
        await handler.stop()