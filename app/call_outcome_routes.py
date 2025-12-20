"""
Coachd.ai - Call Outcome & Admin API Routes
==============================================
API endpoints for:
- Call outcome submission (agents)
- Agency listing (agents)
- Statistics and reports (admin)
- Winning rebuttals management (admin)

Admin endpoints require password authentication.
Set COACHD_ADMIN_PASSWORD environment variable.
"""

import os
from fastapi import APIRouter, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from typing import Optional

from .call_outcomes import (
    CallOutcome, 
    get_outcome_storage, 
    get_agency_config
)
from .winning_rebuttals import (
    get_generator,
    reindex_winning_rebuttals,
    reindex_all_agencies,
    weekly_refresh
)
from .call_outcomes import get_agency_config

# ==================== CONFIG ====================

# Admin password from environment (set in Render)
ADMIN_PASSWORD = os.environ.get("COACHD_ADMIN_PASSWORD", "coachd-admin-2024")

router = APIRouter(tags=["call-outcomes"])


# ==================== AUTH ====================

def verify_admin(x_admin_password: Optional[str] = Header(None)) -> bool:
    """Verify admin password from header"""
    if not x_admin_password or x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required. Provide X-Admin-Password header."
        )
    return True


# ==================== AGENT ENDPOINTS ====================

@router.get("/api/agencies")
async def list_agencies():
    """
    List available agencies for agent selection.
    No auth required - called from identify screen.
    """
    config = get_agency_config()
    agencies = config.get_agencies()
    
    return JSONResponse(content={
        "agencies": agencies
    })


@router.post("/api/call/outcome")
async def submit_call_outcome(outcome: CallOutcome):
    """
    Submit call outcome after a call ends.
    No auth required - called from agent interface.
    """
    storage = get_outcome_storage()
    
    # Validate
    if outcome.outcome not in ['closed', 'not_closed']:
        raise HTTPException(status_code=400, detail="Invalid outcome value")
    
    if not outcome.agent_name:
        raise HTTPException(status_code=400, detail="Agent name required")
    
    if not outcome.agency:
        raise HTTPException(status_code=400, detail="Agency required")
    
    # Save
    success = storage.save_outcome(outcome)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save outcome")
    
    return JSONResponse(content={
        "status": "success",
        "message": "Outcome recorded"
    })


# ==================== ADMIN ENDPOINTS ====================

@router.get("/api/admin/stats")
async def get_overall_stats(
    agency: Optional[str] = None,
    _: bool = Depends(verify_admin)
):
    """
    Get overall statistics.
    Optionally filter by agency.
    
    Requires: X-Admin-Password header
    """
    storage = get_outcome_storage()
    outcomes = storage.get_all_outcomes(agency)
    
    if not outcomes:
        return JSONResponse(content={
            "status": "success",
            "data": {
                "total_calls": 0,
                "closed_calls": 0,
                "close_rate": 0,
                "avg_duration": 0,
                "by_call_type": {}
            }
        })
    
    total = len(outcomes)
    closed = sum(1 for o in outcomes if o.outcome == 'closed')
    
    # By call type
    by_type = {}
    for o in outcomes:
        t = o.call_type
        if t not in by_type:
            by_type[t] = {"total": 0, "closed": 0}
        by_type[t]["total"] += 1
        if o.outcome == 'closed':
            by_type[t]["closed"] += 1
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "total_calls": total,
            "closed_calls": closed,
            "close_rate": round(closed / total * 100, 1) if total > 0 else 0,
            "avg_duration": round(sum(o.duration_seconds for o in outcomes) / total, 0) if total > 0 else 0,
            "by_call_type": by_type,
            "agency_filter": agency
        }
    })


@router.get("/api/admin/agents")
async def get_agent_stats(
    agency: Optional[str] = None,
    _: bool = Depends(verify_admin)
):
    """
    Get per-agent statistics.
    
    Requires: X-Admin-Password header
    """
    storage = get_outcome_storage()
    stats = storage.get_agent_stats(agency)
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "agents": [s.model_dump() for s in stats],
            "count": len(stats)
        }
    })


@router.get("/api/admin/agency/{agency_code}")
async def get_agency_detail(
    agency_code: str,
    _: bool = Depends(verify_admin)
):
    """
    Get detailed stats for a specific agency.
    
    Requires: X-Admin-Password header
    """
    storage = get_outcome_storage()
    stats = storage.get_agency_stats(agency_code)
    
    return JSONResponse(content={
        "status": "success",
        "data": stats.model_dump()
    })


@router.get("/api/admin/objections")
async def get_objections(
    _: bool = Depends(verify_admin)
):
    """
    Get common objections that kill deals.
    
    Requires: X-Admin-Password header
    """
    storage = get_outcome_storage()
    objections = storage.get_common_objections()
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "objections": objections
        }
    })


@router.get("/api/admin/winning-guidance")
async def get_winning_guidance(
    min_success: int = 2,
    _: bool = Depends(verify_admin)
):
    """
    Get guidance that has led to closes.
    
    Requires: X-Admin-Password header
    """
    storage = get_outcome_storage()
    winning = storage.get_winning_guidance(min_success)
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "winning_guidance": [w.model_dump() for w in winning],
            "count": len(winning)
        }
    })


# ==================== SYSTEM DOCUMENT ENDPOINTS ====================

@router.get("/api/admin/winning-rebuttals/status")
async def get_winning_rebuttals_status(
    _: bool = Depends(verify_admin)
):
    """
    Get status of the winning rebuttals system document.
    
    Requires: X-Admin-Password header
    """
    generator = get_generator()
    storage = get_outcome_storage()
    
    should_generate, reason = generator.should_generate()
    meta = generator.get_metadata()
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "document_exists": generator.document_exists(),
            "can_generate": should_generate,
            "reason": reason,
            "total_outcomes": storage.get_outcome_count(),
            "metadata": meta
        }
    })


@router.post("/api/admin/winning-rebuttals/generate")
async def generate_winning_rebuttals(
    force: bool = False,
    _: bool = Depends(verify_admin)
):
    """
    Generate/update the winning rebuttals document.
    
    Args:
        force: Generate even if threshold not met
    
    Requires: X-Admin-Password header
    """
    generator = get_generator()
    result = generator.generate(force=force)
    
    return JSONResponse(content={
        "status": "success" if result.get('success') else "error",
        "data": result
    })


@router.get("/api/admin/winning-rebuttals/document")
async def get_winning_rebuttals_document(
    _: bool = Depends(verify_admin)
):
    """
    Get the current winning rebuttals document content.
    
    Requires: X-Admin-Password header
    """
    generator = get_generator()
    
    if not generator.document_exists():
        raise HTTPException(status_code=404, detail="Document not generated yet")
    
    content = generator.get_document()
    meta = generator.get_metadata()
    
    return JSONResponse(content={
        "status": "success",
        "data": {
            "content": content,
            "metadata": meta
        }
    })


@router.post("/api/admin/winning-rebuttals/reindex")
async def reindex_winning_rebuttals_endpoint(
    agency: Optional[str] = None,
    all_agencies: bool = False,
    _: bool = Depends(verify_admin)
):
    """
    Re-index the winning rebuttals document into ChromaDB.
    
    CRITICAL: This indexes into agency-specific collections so RAG actually finds the rebuttals.
    
    Args:
        agency: Specific agency to index for (e.g., "ADERHOLT")
        all_agencies: If True, index into ALL agency collections
    
    Requires: X-Admin-Password header
    """
    try:
        from .vector_db import get_vector_db
        db = get_vector_db()
        
        if all_agencies:
            # Get all agencies and index to each
            config = get_agency_config()
            agencies = [a['code'] for a in config.get_agencies()]
            result = reindex_all_agencies(db.client, agencies)
        else:
            # Index to specific agency or shared
            result = reindex_winning_rebuttals(db.client, agency=agency)
        
        return JSONResponse(content={
            "status": "success" if result.get('success') else "error",
            "data": result
        })
    except ImportError:
        return JSONResponse(content={
            "status": "error",
            "message": "ChromaDB not configured"
        }, status_code=500)
    except Exception as e:
        return JSONResponse(content={
            "status": "error",
            "message": str(e)
        }, status_code=500)


@router.post("/api/admin/winning-rebuttals/refresh")
async def full_refresh(
    agency: Optional[str] = None,
    _: bool = Depends(verify_admin)
):
    """
    Full refresh: generate document and re-index to agency collections.
    This is what the weekly scheduled task calls.
    
    By default, indexes to ALL agencies. Pass agency param to target one.
    
    Requires: X-Admin-Password header
    """
    try:
        from .vector_db import get_vector_db
        db = get_vector_db()
        
        # Get agencies list
        if agency:
            agencies = [agency]
        else:
            # Default: refresh for all agencies
            config = get_agency_config()
            agencies = [a['code'] for a in config.get_agencies()]
        
        result = weekly_refresh(db.client, agencies)
        return JSONResponse(content={
            "status": "success" if result.get('success') else "error",
            "data": result
        })
    except ImportError:
        # Just generate without indexing
        generator = get_generator()
        result = generator.generate()
        return JSONResponse(content={
            "status": "success" if result.get('success') else "error",
            "data": result,
            "note": "Generated but not indexed - ChromaDB not configured"
        })
    except Exception as e:
        return JSONResponse(content={
            "status": "error", 
            "message": str(e)
        }, status_code=500)


# ==================== AGENCY MANAGEMENT ====================

@router.post("/api/admin/agencies")
async def add_agency(
    code: str,
    name: str,
    _: bool = Depends(verify_admin)
):
    """
    Add a new agency.
    
    Requires: X-Admin-Password header
    """
    config = get_agency_config()
    success = config.add_agency(code, name)
    
    if not success:
        raise HTTPException(status_code=400, detail="Agency code already exists")
    
    return JSONResponse(content={
        "status": "success",
        "message": f"Agency '{name}' ({code}) added"
    })