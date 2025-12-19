"""
Coachd Usage Tracker
Pulls usage data from external APIs and calculates costs
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .database import log_usage, is_db_configured, get_db, ExternalServiceSnapshot


# ============ PRICING CONSTANTS ============
# Updated pricing as of 2024 - adjust as needed

PRICING = {
    'deepgram': {
        'nova-2': 0.0043,  # per minute
        'nova': 0.0040,
        'enhanced': 0.0145,
        'base': 0.0125,
    },
    'twilio': {
        'call_per_minute': 0.014,  # outbound to US/Canada
        'phone_number_monthly': 1.15,
        'recording_per_minute': 0.0025,
    },
    'claude': {
        'claude-sonnet-4-20250514': {
            'input_per_1k': 0.003,
            'output_per_1k': 0.015,
        },
        'claude-3-5-sonnet-20241022': {
            'input_per_1k': 0.003,
            'output_per_1k': 0.015,
        },
        'claude-3-haiku-20240307': {
            'input_per_1k': 0.00025,
            'output_per_1k': 0.00125,
        },
    },
    'render': {
        'starter_monthly': 7.00,  # PostgreSQL starter
        'web_service_free': 0.00,
        'web_service_starter': 7.00,
    }
}


# ============ COST CALCULATION HELPERS ============

def calculate_deepgram_cost(minutes: float, model: str = 'nova-2') -> float:
    """Calculate Deepgram transcription cost"""
    rate = PRICING['deepgram'].get(model, PRICING['deepgram']['nova-2'])
    return minutes * rate


def calculate_twilio_cost(call_minutes: float, recording_minutes: float = 0) -> float:
    """Calculate Twilio call cost"""
    call_cost = call_minutes * PRICING['twilio']['call_per_minute']
    recording_cost = recording_minutes * PRICING['twilio']['recording_per_minute']
    return call_cost + recording_cost


def calculate_claude_cost(input_tokens: int, output_tokens: int, model: str = 'claude-sonnet-4-20250514') -> float:
    """Calculate Claude API cost"""
    model_pricing = PRICING['claude'].get(model, PRICING['claude']['claude-sonnet-4-20250514'])
    input_cost = (input_tokens / 1000) * model_pricing['input_per_1k']
    output_cost = (output_tokens / 1000) * model_pricing['output_per_1k']
    return input_cost + output_cost


# ============ LOGGING WRAPPERS ============
# Use these throughout the app to automatically track usage

def log_deepgram_usage(
    duration_seconds: float,
    agency_code: Optional[str] = None,
    session_id: Optional[str] = None,
    model: str = 'nova-2'
):
    """Log Deepgram transcription usage"""
    minutes = duration_seconds / 60
    cost = calculate_deepgram_cost(minutes, model)
    
    log_usage(
        service='deepgram',
        operation='transcribe',
        quantity=minutes,
        unit='minutes',
        estimated_cost=cost,
        agency_code=agency_code,
        session_id=session_id,
        metadata={'model': model, 'seconds': duration_seconds}
    )
    
    return cost


def log_twilio_usage(
    call_duration_seconds: float,
    agency_code: Optional[str] = None,
    session_id: Optional[str] = None,
    recording_seconds: float = 0,
    call_sid: Optional[str] = None
):
    """Log Twilio call usage"""
    call_minutes = call_duration_seconds / 60
    recording_minutes = recording_seconds / 60
    cost = calculate_twilio_cost(call_minutes, recording_minutes)
    
    log_usage(
        service='twilio',
        operation='call',
        quantity=call_minutes,
        unit='minutes',
        estimated_cost=cost,
        agency_code=agency_code,
        session_id=session_id,
        metadata={
            'call_sid': call_sid,
            'recording_minutes': recording_minutes
        }
    )
    
    return cost


def log_claude_usage(
    input_tokens: int,
    output_tokens: int,
    agency_code: Optional[str] = None,
    session_id: Optional[str] = None,
    model: str = 'claude-sonnet-4-20250514',
    operation: str = 'completion'
):
    """Log Claude API usage"""
    cost = calculate_claude_cost(input_tokens, output_tokens, model)
    total_tokens = input_tokens + output_tokens
    
    log_usage(
        service='claude',
        operation=operation,
        quantity=total_tokens,
        unit='tokens',
        estimated_cost=cost,
        agency_code=agency_code,
        session_id=session_id,
        metadata={
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens
        }
    )
    
    return cost


# ============ EXTERNAL API FETCHERS ============

def fetch_anthropic_usage() -> Dict[str, Any]:
    """
    Fetch usage from Anthropic API
    Note: Anthropic doesn't have a public usage API yet,
    so we rely on our internal logging. This is a placeholder.
    """
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    
    # Anthropic doesn't expose a usage API publicly yet
    # Return empty dict - we track usage internally
    return {
        'source': 'internal_tracking',
        'note': 'Anthropic usage tracked via internal logging',
        'fetched_at': datetime.utcnow().isoformat()
    }


def fetch_deepgram_usage() -> Dict[str, Any]:
    """
    Fetch usage from Deepgram API
    https://developers.deepgram.com/reference/get-all-balances
    """
    api_key = os.getenv('DEEPGRAM_API_KEY', '')
    
    if not api_key:
        return {'error': 'DEEPGRAM_API_KEY not configured'}
    
    try:
        # Get project balances
        headers = {
            'Authorization': f'Token {api_key}',
            'Content-Type': 'application/json'
        }
        
        # First get projects
        projects_response = requests.get(
            'https://api.deepgram.com/v1/projects',
            headers=headers,
            timeout=10
        )
        
        if projects_response.status_code != 200:
            return {'error': f'Failed to fetch projects: {projects_response.status_code}'}
        
        projects_data = projects_response.json()
        projects = projects_data.get('projects', [])
        
        if not projects:
            return {'error': 'No projects found'}
        
        # Get usage for first project
        project_id = projects[0]['project_id']
        
        # Get balances
        balances_response = requests.get(
            f'https://api.deepgram.com/v1/projects/{project_id}/balances',
            headers=headers,
            timeout=10
        )
        
        balances_data = {}
        if balances_response.status_code == 200:
            balances_data = balances_response.json()
        
        # Get usage summary (last 30 days)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        usage_response = requests.get(
            f'https://api.deepgram.com/v1/projects/{project_id}/usage',
            headers=headers,
            params={
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            timeout=10
        )
        
        usage_data = {}
        if usage_response.status_code == 200:
            usage_data = usage_response.json()
        
        return {
            'project_id': project_id,
            'balances': balances_data,
            'usage_30d': usage_data,
            'fetched_at': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {'error': str(e)}


def fetch_twilio_usage() -> Dict[str, Any]:
    """
    Fetch usage from Twilio API
    https://www.twilio.com/docs/usage/api/usage-record
    """
    account_sid = os.getenv('TWILIO_ACCOUNT_SID', '')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN', '')
    
    if not account_sid or not auth_token:
        return {'error': 'Twilio credentials not configured'}
    
    try:
        # Get this month's usage
        end_date = datetime.utcnow()
        start_date = end_date.replace(day=1)  # First of month
        
        response = requests.get(
            f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Usage/Records/ThisMonth.json',
            auth=(account_sid, auth_token),
            timeout=10
        )
        
        if response.status_code != 200:
            return {'error': f'Failed to fetch usage: {response.status_code}'}
        
        data = response.json()
        usage_records = data.get('usage_records', [])
        
        # Parse relevant records
        summary = {
            'calls': {},
            'recordings': {},
            'sms': {},
            'phone_numbers': {},
            'total_cost': 0
        }
        
        for record in usage_records:
            category = record.get('category', '')
            price = float(record.get('price', 0) or 0)
            summary['total_cost'] += price
            
            if 'calls' in category.lower():
                summary['calls'][category] = {
                    'count': record.get('count', 0),
                    'usage': record.get('usage', 0),
                    'unit': record.get('usage_unit', ''),
                    'price': price
                }
            elif 'recording' in category.lower():
                summary['recordings'][category] = {
                    'count': record.get('count', 0),
                    'usage': record.get('usage', 0),
                    'price': price
                }
            elif 'phonenumber' in category.lower():
                summary['phone_numbers'][category] = {
                    'count': record.get('count', 0),
                    'price': price
                }
        
        return {
            'period': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            'summary': summary,
            'raw_records_count': len(usage_records),
            'fetched_at': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {'error': str(e)}


def fetch_render_usage() -> Dict[str, Any]:
    """
    Fetch usage from Render API
    https://api-docs.render.com/reference/get-bandwidth
    """
    api_key = os.getenv('RENDER_API_KEY', '')
    
    if not api_key:
        return {'error': 'RENDER_API_KEY not configured'}
    
    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json'
        }
        
        # Get services
        services_response = requests.get(
            'https://api.render.com/v1/services',
            headers=headers,
            params={'limit': 20},
            timeout=10
        )
        
        if services_response.status_code != 200:
            return {'error': f'Failed to fetch services: {services_response.status_code}'}
        
        services_data = services_response.json()
        services = []
        
        for item in services_data:
            service = item.get('service', {})
            services.append({
                'id': service.get('id'),
                'name': service.get('name'),
                'type': service.get('type'),
                'status': service.get('suspended', 'active'),
                'created_at': service.get('createdAt')
            })
        
        # Get bandwidth for each service
        for svc in services:
            if svc['id']:
                try:
                    bw_response = requests.get(
                        f"https://api.render.com/v1/services/{svc['id']}/metrics/bandwidth",
                        headers=headers,
                        params={'resolution': 'day', 'numPeriods': 30},
                        timeout=10
                    )
                    if bw_response.status_code == 200:
                        svc['bandwidth'] = bw_response.json()
                except:
                    pass
        
        return {
            'services': services,
            'fetched_at': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {'error': str(e)}


def fetch_all_external_usage() -> Dict[str, Any]:
    """Fetch usage from all external services"""
    return {
        'anthropic': fetch_anthropic_usage(),
        'deepgram': fetch_deepgram_usage(),
        'twilio': fetch_twilio_usage(),
        'render': fetch_render_usage(),
        'fetched_at': datetime.utcnow().isoformat()
    }


def save_external_snapshot(service: str, data: Dict[str, Any]):
    """Save an external service snapshot to the database"""
    if not is_db_configured():
        return
    
    try:
        with get_db() as db:
            snapshot = ExternalServiceSnapshot(
                service=service,
                data_json=json.dumps(data),
                total_cost=data.get('summary', {}).get('total_cost') if isinstance(data.get('summary'), dict) else None
            )
            db.add(snapshot)
    except Exception as e:
        print(f"Failed to save snapshot: {e}")


def get_platform_summary() -> Dict[str, Any]:
    """
    Get complete platform summary for the admin dashboard
    Combines internal tracking with external API data
    """
    from .database import get_usage_summary, get_usage_by_agency, get_daily_usage
    
    # Get internal usage data
    internal_summary = get_usage_summary()
    agency_breakdown = get_usage_by_agency()
    daily_data = get_daily_usage(days=30)
    
    # Fetch external data
    external_data = fetch_all_external_usage()
    
    # Calculate totals
    total_internal_cost = sum(
        svc.get('total_cost', 0) 
        for svc in internal_summary.values()
    )
    
    # Twilio external cost (most accurate)
    twilio_external_cost = 0
    if 'twilio' in external_data and 'summary' in external_data['twilio']:
        twilio_external_cost = external_data['twilio']['summary'].get('total_cost', 0)
    
    return {
        'internal': {
            'by_service': internal_summary,
            'by_agency': agency_breakdown,
            'total_cost': total_internal_cost
        },
        'external': external_data,
        'daily_trends': daily_data,
        'totals': {
            'estimated_monthly': total_internal_cost,
            'twilio_actual': twilio_external_cost
        },
        'generated_at': datetime.utcnow().isoformat()
    }
