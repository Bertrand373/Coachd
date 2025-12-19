"""
Coachd.ai Application Package
"""

from .config import settings
from .document_processor import DocumentProcessor
from .vector_db import VectorDatabase, get_vector_db
from .rag_engine import RAGEngine, get_rag_engine, CallContext
from .database import init_db, is_db_configured, log_usage
from .usage_tracker import log_claude_usage, log_deepgram_usage, log_twilio_usage

__all__ = [
    "settings",
    "DocumentProcessor", 
    "VectorDatabase",
    "get_vector_db",
    "RAGEngine",
    "get_rag_engine",
    "CallContext",
    "init_db",
    "is_db_configured",
    "log_usage",
    "log_claude_usage",
    "log_deepgram_usage",
    "log_twilio_usage"
]
