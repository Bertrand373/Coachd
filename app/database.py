"""
Coachd Database Setup
PostgreSQL connection and models for usage tracking
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Index, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

# Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Handle Render's postgres:// vs postgresql:// issue
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create engine and session
engine = None
SessionLocal = None
Base = declarative_base()

if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print("✓ Database engine created")
    except Exception as e:
        print(f"✗ Database connection failed: {e}")


class UsageLog(Base):
    """
    Tracks every API call for cost attribution
    """
    __tablename__ = "usage_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Attribution
    agency_code = Column(String(50), index=True)
    agent_id = Column(String(100), nullable=True)
    session_id = Column(String(100), nullable=True)
    
    # Service details
    service = Column(String(50), index=True)  # deepgram, twilio, claude, render
    operation = Column(String(100))  # transcribe, call, completion, etc.
    
    # Metrics
    quantity = Column(Float, default=0)  # minutes, tokens, bytes, etc.
    unit = Column(String(20))  # minutes, tokens, bytes, calls
    
    # Cost
    estimated_cost = Column(Float, default=0)  # USD
    
    # Additional context
    metadata_json = Column(Text, nullable=True)  # JSON string for extra details
    
    __table_args__ = (
        Index('idx_agency_service_timestamp', 'agency_code', 'service', 'timestamp'),
        Index('idx_service_timestamp', 'service', 'timestamp'),
    )


class DailyAggregate(Base):
    """
    Pre-aggregated daily stats for faster dashboard queries
    """
    __tablename__ = "daily_aggregates"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, index=True)
    agency_code = Column(String(50), index=True)
    service = Column(String(50), index=True)
    
    # Aggregated metrics
    total_quantity = Column(Float, default=0)
    total_cost = Column(Float, default=0)
    request_count = Column(Integer, default=0)
    
    __table_args__ = (
        Index('idx_daily_agency_service', 'date', 'agency_code', 'service'),
    )


class ExternalServiceSnapshot(Base):
    """
    Stores snapshots from external API usage endpoints
    """
    __tablename__ = "external_snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    service = Column(String(50), index=True)  # deepgram, twilio, claude, render
    
    # Raw data from external API
    data_json = Column(Text)
    
    # Parsed summary
    total_usage = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)


class PlatformConfig(Base):
    """
    Platform-wide configuration settings (persisted server-side)
    Used for things like Render tier selections that affect billing calculations.
    """
    __tablename__ = "platform_config"
    
    key = Column(String(100), primary_key=True)
    value = Column(Text)  # JSON string for complex values
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(100), nullable=True)  # Optional: track who changed it


def init_db():
    """Initialize database tables"""
    if engine:
        Base.metadata.create_all(bind=engine)
        print("✓ Database tables initialized")
        return True
    return False


@contextmanager
def get_db() -> Session:
    """Get database session with automatic cleanup"""
    if not SessionLocal:
        raise RuntimeError("Database not configured")
    
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def is_db_configured() -> bool:
    """Check if database is properly configured"""
    return engine is not None and SessionLocal is not None


# ============ USAGE LOGGING FUNCTIONS ============

def log_usage(
    service: str,
    operation: str,
    quantity: float,
    unit: str,
    estimated_cost: float = 0,
    agency_code: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict] = None
):
    """
    Log a usage event to the database.
    Call this from anywhere in the app when making API calls.
    """
    if not is_db_configured():
        return None
    
    try:
        with get_db() as db:
            log_entry = UsageLog(
                timestamp=datetime.utcnow(),
                agency_code=agency_code,
                agent_id=agent_id,
                session_id=session_id,
                service=service,
                operation=operation,
                quantity=quantity,
                unit=unit,
                estimated_cost=estimated_cost,
                metadata_json=json.dumps(metadata) if metadata else None
            )
            db.add(log_entry)
            return log_entry.id
    except Exception as e:
        print(f"Failed to log usage: {e}")
        return None


def get_usage_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    agency_code: Optional[str] = None
) -> Dict[str, Any]:
    """Get usage summary grouped by service"""
    if not is_db_configured():
        return {}
    
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=30)
    if not end_date:
        end_date = datetime.utcnow()
    
    try:
        with get_db() as db:
            query = db.query(
                UsageLog.service,
                func.sum(UsageLog.quantity).label('total_quantity'),
                func.sum(UsageLog.estimated_cost).label('total_cost'),
                func.count(UsageLog.id).label('request_count')
            ).filter(
                UsageLog.timestamp >= start_date,
                UsageLog.timestamp <= end_date
            )
            
            if agency_code:
                query = query.filter(UsageLog.agency_code == agency_code)
            
            results = query.group_by(UsageLog.service).all()
            
            summary = {}
            for row in results:
                summary[row.service] = {
                    'total_quantity': float(row.total_quantity or 0),
                    'total_cost': float(row.total_cost or 0),
                    'request_count': int(row.request_count or 0)
                }
            
            return summary
    except Exception as e:
        print(f"Failed to get usage summary: {e}")
        return {}


def get_usage_by_agency(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Dict[str, Any]]:
    """Get usage breakdown by agency"""
    if not is_db_configured():
        return {}
    
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=30)
    if not end_date:
        end_date = datetime.utcnow()
    
    try:
        with get_db() as db:
            results = db.query(
                UsageLog.agency_code,
                UsageLog.service,
                func.sum(UsageLog.quantity).label('total_quantity'),
                func.sum(UsageLog.estimated_cost).label('total_cost'),
                func.count(UsageLog.id).label('request_count')
            ).filter(
                UsageLog.timestamp >= start_date,
                UsageLog.timestamp <= end_date
            ).group_by(
                UsageLog.agency_code,
                UsageLog.service
            ).all()
            
            agencies = {}
            for row in results:
                agency = row.agency_code or 'unknown'
                if agency not in agencies:
                    agencies[agency] = {'services': {}, 'total_cost': 0}
                
                agencies[agency]['services'][row.service] = {
                    'quantity': float(row.total_quantity or 0),
                    'cost': float(row.total_cost or 0),
                    'requests': int(row.request_count or 0)
                }
                agencies[agency]['total_cost'] += float(row.total_cost or 0)
            
            return agencies
    except Exception as e:
        print(f"Failed to get agency usage: {e}")
        return {}


def get_daily_usage(
    days: int = 30,
    agency_code: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get daily usage for charts"""
    if not is_db_configured():
        return []
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    try:
        with get_db() as db:
            query = db.query(
                func.date(UsageLog.timestamp).label('date'),
                UsageLog.service,
                func.sum(UsageLog.estimated_cost).label('cost')
            ).filter(
                UsageLog.timestamp >= start_date
            )
            
            if agency_code:
                query = query.filter(UsageLog.agency_code == agency_code)
            
            results = query.group_by(
                func.date(UsageLog.timestamp),
                UsageLog.service
            ).order_by(func.date(UsageLog.timestamp)).all()
            
            daily_data = []
            for row in results:
                daily_data.append({
                    'date': row.date.isoformat() if row.date else None,
                    'service': row.service,
                    'cost': float(row.cost or 0)
                })
            
            return daily_data
    except Exception as e:
        print(f"Failed to get daily usage: {e}")
        return []


def get_recent_logs(limit: int = 100, agency_code: Optional[str] = None) -> List[Dict]:
    """Get recent usage logs"""
    if not is_db_configured():
        return []
    
    try:
        with get_db() as db:
            query = db.query(UsageLog).order_by(UsageLog.timestamp.desc())
            
            if agency_code:
                query = query.filter(UsageLog.agency_code == agency_code)
            
            results = query.limit(limit).all()
            
            logs = []
            for log in results:
                logs.append({
                    'id': log.id,
                    'timestamp': log.timestamp.isoformat() if log.timestamp else None,
                    'agency': log.agency_code,
                    'service': log.service,
                    'operation': log.operation,
                    'quantity': log.quantity,
                    'unit': log.unit,
                    'cost': log.estimated_cost
                })
            
            return logs
    except Exception as e:
        print(f"Failed to get recent logs: {e}")
        return []


# ============ PLATFORM CONFIG FUNCTIONS ============

def get_platform_config(key: str, default: Any = None) -> Any:
    """
    Get a platform config value by key.
    Returns parsed JSON if the value is JSON, otherwise returns raw string.
    """
    if not is_db_configured():
        return default
    
    try:
        with get_db() as db:
            config = db.query(PlatformConfig).filter(PlatformConfig.key == key).first()
            
            if config is None:
                return default
            
            # Try to parse as JSON
            try:
                return json.loads(config.value)
            except (json.JSONDecodeError, TypeError):
                return config.value
                
    except Exception as e:
        print(f"Failed to get platform config '{key}': {e}")
        return default


def set_platform_config(key: str, value: Any, updated_by: Optional[str] = None) -> bool:
    """
    Set a platform config value.
    Complex values (dicts, lists) are stored as JSON strings.
    """
    if not is_db_configured():
        return False
    
    try:
        # Convert to JSON string if not already a string
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
        else:
            value_str = str(value)
        
        with get_db() as db:
            config = db.query(PlatformConfig).filter(PlatformConfig.key == key).first()
            
            if config:
                # Update existing
                config.value = value_str
                config.updated_at = datetime.utcnow()
                config.updated_by = updated_by
            else:
                # Create new
                config = PlatformConfig(
                    key=key,
                    value=value_str,
                    updated_by=updated_by
                )
                db.add(config)
            
            return True
            
    except Exception as e:
        print(f"Failed to set platform config '{key}': {e}")
        return False


def get_all_platform_config() -> Dict[str, Any]:
    """Get all platform config values as a dictionary."""
    if not is_db_configured():
        return {}
    
    try:
        with get_db() as db:
            configs = db.query(PlatformConfig).all()
            
            result = {}
            for config in configs:
                try:
                    result[config.key] = json.loads(config.value)
                except (json.JSONDecodeError, TypeError):
                    result[config.key] = config.value
            
            return result
            
    except Exception as e:
        print(f"Failed to get all platform config: {e}")
        return {}