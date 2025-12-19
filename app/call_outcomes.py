"""
Coachd.ai - Call Outcome Tracking
====================================
Tracks call outcomes, agent performance, and guidance effectiveness.

Data is stored in JSON Lines format for easy processing.
In production, replace with PostgreSQL.
"""

from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel
import json
import os

# ==================== DATA MODELS ====================

class CallOutcome(BaseModel):
    """Data model for call outcome submission"""
    client_id: str
    duration_seconds: int
    outcome: str  # 'closed' or 'not_closed'
    guidance_count: int
    guidance_used: List[str]
    most_helpful_index: Optional[int] = None
    most_helpful_text: Optional[str] = None
    final_objection: Optional[str] = None
    timestamp: str
    agency: str
    agent_name: str
    agent_id: Optional[str] = None
    call_type: str = "browser_test"  # 'browser_test' or 'twilio_live'


class AgentStats(BaseModel):
    """Per-agent statistics"""
    agent_name: str
    agency: str
    total_calls: int
    closed_calls: int
    close_rate: float
    avg_duration: float
    most_used_tip: Optional[str] = None


class AgencyStats(BaseModel):
    """Per-agency statistics"""
    agency: str
    total_calls: int
    closed_calls: int
    close_rate: float
    avg_duration: float
    top_agents: List[AgentStats]
    top_objections: List[Dict]


class WinningGuidance(BaseModel):
    """Guidance that led to closes"""
    text: str
    success_count: int
    total_uses: int
    success_rate: float
    agencies: List[str]


# ==================== STORAGE ====================

class OutcomeStorage:
    """
    File-based storage for call outcomes.
    Replace with PostgreSQL in production.
    """
    
    def __init__(self, storage_path: str = None):
        # Use /data on Render, fallback to local
        if storage_path is None:
            if os.path.exists("/data"):
                storage_path = "/data/call_outcomes"
            else:
                storage_path = "./call_outcomes"
        self.storage_path = storage_path
        self.outcomes_file = os.path.join(storage_path, "outcomes.jsonl")
        self._ensure_storage()
    
    def _ensure_storage(self):
        """Ensure storage directory and file exist"""
        os.makedirs(self.storage_path, exist_ok=True)
        if not os.path.exists(self.outcomes_file):
            open(self.outcomes_file, 'a').close()
    
    def save_outcome(self, outcome: CallOutcome) -> bool:
        """Append outcome to storage"""
        try:
            with open(self.outcomes_file, 'a') as f:
                f.write(outcome.model_dump_json() + '\n')
            print(f"[OUTCOME] Saved: {outcome.agent_name} | {outcome.outcome} | {outcome.agency}")
            return True
        except Exception as e:
            print(f"[OUTCOME] Error saving: {e}")
            return False
    
    def get_all_outcomes(self, agency: Optional[str] = None) -> List[CallOutcome]:
        """Load all outcomes, optionally filtered by agency"""
        outcomes = []
        try:
            with open(self.outcomes_file, 'r') as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            outcome = CallOutcome(**data)
                            if agency is None or outcome.agency == agency:
                                outcomes.append(outcome)
                        except:
                            continue
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[OUTCOME] Error loading: {e}")
        return outcomes
    
    def get_agent_stats(self, agency: Optional[str] = None) -> List[AgentStats]:
        """Get per-agent statistics"""
        outcomes = self.get_all_outcomes(agency)
        
        # Group by agent
        agent_data = {}
        for o in outcomes:
            key = f"{o.agent_name}|{o.agency}"
            if key not in agent_data:
                agent_data[key] = {
                    'name': o.agent_name,
                    'agency': o.agency,
                    'calls': [],
                    'tips': {}
                }
            agent_data[key]['calls'].append(o)
            
            # Track most helpful tip
            if o.most_helpful_text:
                tip = o.most_helpful_text[:80]
                agent_data[key]['tips'][tip] = agent_data[key]['tips'].get(tip, 0) + 1
        
        # Calculate stats
        stats = []
        for key, data in agent_data.items():
            calls = data['calls']
            total = len(calls)
            closed = sum(1 for c in calls if c.outcome == 'closed')
            
            most_used = None
            if data['tips']:
                most_used = max(data['tips'].items(), key=lambda x: x[1])[0]
            
            stats.append(AgentStats(
                agent_name=data['name'],
                agency=data['agency'],
                total_calls=total,
                closed_calls=closed,
                close_rate=round(closed / total * 100, 1) if total > 0 else 0,
                avg_duration=round(sum(c.duration_seconds for c in calls) / total, 0) if total > 0 else 0,
                most_used_tip=most_used
            ))
        
        # Sort by close rate
        stats.sort(key=lambda x: x.close_rate, reverse=True)
        return stats
    
    def get_agency_stats(self, agency: str) -> AgencyStats:
        """Get statistics for a specific agency"""
        outcomes = self.get_all_outcomes(agency)
        
        if not outcomes:
            return AgencyStats(
                agency=agency,
                total_calls=0,
                closed_calls=0,
                close_rate=0,
                avg_duration=0,
                top_agents=[],
                top_objections=[]
            )
        
        total = len(outcomes)
        closed = sum(1 for o in outcomes if o.outcome == 'closed')
        
        # Top objections
        objection_counts = {}
        for o in outcomes:
            if o.outcome == 'not_closed' and o.final_objection:
                objection_counts[o.final_objection] = objection_counts.get(o.final_objection, 0) + 1
        
        top_objections = [
            {"objection": k, "count": v}
            for k, v in sorted(objection_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        
        return AgencyStats(
            agency=agency,
            total_calls=total,
            closed_calls=closed,
            close_rate=round(closed / total * 100, 1) if total > 0 else 0,
            avg_duration=round(sum(o.duration_seconds for o in outcomes) / total, 0),
            top_agents=self.get_agent_stats(agency)[:5],
            top_objections=top_objections
        )
    
    def get_winning_guidance(self, min_success_count: int = 2) -> List[WinningGuidance]:
        """
        Get guidance that has led to closed deals.
        This is the core data for the auto-updating master document.
        """
        outcomes = self.get_all_outcomes()
        
        # Track guidance effectiveness
        guidance_data = {}
        
        for o in outcomes:
            for tip in o.guidance_used:
                # Normalize tip text
                tip_key = tip.strip()[:200]
                
                if tip_key not in guidance_data:
                    guidance_data[tip_key] = {
                        'text': tip,
                        'success': 0,
                        'total': 0,
                        'agencies': set()
                    }
                
                guidance_data[tip_key]['total'] += 1
                guidance_data[tip_key]['agencies'].add(o.agency)
                
                if o.outcome == 'closed':
                    guidance_data[tip_key]['success'] += 1
                    
                    # Extra weight if marked as "most helpful"
                    if o.most_helpful_text and tip_key in o.most_helpful_text:
                        guidance_data[tip_key]['success'] += 0.5
        
        # Build results
        results = []
        for key, data in guidance_data.items():
            if data['success'] >= min_success_count:
                results.append(WinningGuidance(
                    text=data['text'],
                    success_count=int(data['success']),
                    total_uses=data['total'],
                    success_rate=round(data['success'] / data['total'] * 100, 1) if data['total'] > 0 else 0,
                    agencies=list(data['agencies'])
                ))
        
        # Sort by success rate, then by count
        results.sort(key=lambda x: (x.success_rate, x.success_count), reverse=True)
        return results
    
    def get_common_objections(self) -> List[Dict]:
        """Get objections that most commonly kill deals"""
        outcomes = self.get_all_outcomes()
        
        objection_counts = {}
        for o in outcomes:
            if o.outcome == 'not_closed' and o.final_objection:
                obj = o.final_objection
                objection_counts[obj] = objection_counts.get(obj, 0) + 1
        
        total_lost = sum(1 for o in outcomes if o.outcome == 'not_closed')
        
        results = []
        for obj, count in sorted(objection_counts.items(), key=lambda x: x[1], reverse=True):
            results.append({
                "objection": obj,
                "count": count,
                "percentage": round(count / total_lost * 100, 1) if total_lost > 0 else 0
            })
        
        return results
    
    def get_outcome_count(self) -> int:
        """Get total number of outcomes for threshold checking"""
        return len(self.get_all_outcomes())


# ==================== AGENCY CONFIG ====================

class AgencyConfig:
    """
    Simple agency configuration.
    In production, this would be in the database.
    """
    
    def __init__(self, config_path: str = None):
        if config_path is None:
            if os.path.exists("/data"):
                config_path = "/data/agencies.json"
            else:
                config_path = "./agencies.json"
        self.config_path = config_path
        self._ensure_config()
    
    def _ensure_config(self):
        """Create default config if doesn't exist"""
        if not os.path.exists(self.config_path):
            default = {
                "agencies": [
                    {"code": "ADERHOLT", "name": "Aderholt Agency"},
                    {"code": "BROOKS", "name": "Brooks Agency"}
                ]
            }
            try:
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            except:
                pass  # Might fail if it's in current directory
            with open(self.config_path, 'w') as f:
                json.dump(default, f, indent=2)
    
    def get_agencies(self) -> List[Dict]:
        """Get list of agencies"""
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
                return data.get('agencies', [])
        except:
            return [
                {"code": "ADERHOLT", "name": "Aderholt Agency"},
                {"code": "BROOKS", "name": "Brooks Agency"}
            ]
    
    def add_agency(self, code: str, name: str) -> bool:
        """Add a new agency"""
        try:
            agencies = self.get_agencies()
            
            # Check for duplicate
            if any(a['code'] == code for a in agencies):
                return False
            
            agencies.append({"code": code, "name": name})
            
            with open(self.config_path, 'w') as f:
                json.dump({"agencies": agencies}, f, indent=2)
            
            return True
        except:
            return False


# ==================== SINGLETONS ====================

_storage_instance = None
_agency_config_instance = None

def get_outcome_storage() -> OutcomeStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = OutcomeStorage()
    return _storage_instance

def get_agency_config() -> AgencyConfig:
    global _agency_config_instance
    if _agency_config_instance is None:
        _agency_config_instance = AgencyConfig()
    return _agency_config_instance
