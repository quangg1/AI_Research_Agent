"""HITL timeout management to prevent indefinite interrupt hangs.

Addresses Issue #9: Interrupts (plan_gate, hitl, memo_gate) can hang forever
if user abandons, causing DB bloat and UX confusion.

Strategy: 48h hard timeout + email notification + graceful cleanup

Usage:
    # Background job (cron)
    python -m app.maintenance.cleanup_stale_interrupts
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any


# Timeout configuration
INTERRUPT_TTL_HOURS = int(os.getenv("INTERRUPT_TTL_HOURS", "48"))  # 48h default
REMINDER_AFTER_HOURS = int(os.getenv("INTERRUPT_REMINDER_HOURS", "2"))  # Remind after 2h
ARCHIVE_AFTER_DAYS = int(os.getenv("INTERRUPT_ARCHIVE_DAYS", "7"))  # Archive after 7 days


def get_interrupt_age(interrupted_at: datetime) -> timedelta:
    """Calculate how long interrupt has been pending."""
    return datetime.utcnow() - interrupted_at


def should_send_reminder(interrupted_at: datetime, reminded: bool) -> bool:
    """Check if reminder should be sent."""
    if reminded:
        return False
    
    age = get_interrupt_age(interrupted_at)
    return age.total_seconds() > REMINDER_AFTER_HOURS * 3600


def should_cancel_interrupt(interrupted_at: datetime) -> bool:
    """Check if interrupt should be auto-canceled due to timeout."""
    age = get_interrupt_age(interrupted_at)
    return age.total_seconds() > INTERRUPT_TTL_HOURS * 3600


def should_archive_run(interrupted_at: datetime) -> bool:
    """Check if run should be archived (soft delete)."""
    age = get_interrupt_age(interrupted_at)
    return age.total_seconds() > ARCHIVE_AFTER_DAYS * 24 * 3600


def explain_timeout(interrupted_at: datetime) -> dict[str, Any]:
    """Generate human-readable timeout explanation."""
    age = get_interrupt_age(interrupted_at)
    hours_remaining = INTERRUPT_TTL_HOURS - (age.total_seconds() / 3600)
    
    return {
        "age_hours": age.total_seconds() / 3600,
        "ttl_hours": INTERRUPT_TTL_HOURS,
        "hours_remaining": max(0, hours_remaining),
        "expired": hours_remaining <= 0,
        "message": (
            f"This research has been waiting for approval for {age.total_seconds() / 3600:.1f} hours. "
            f"It will expire in {max(0, hours_remaining):.1f} hours."
            if hours_remaining > 0
            else "This research has expired. Please start a new research."
        )
    }


def cleanup_stale_interrupts_summary(db_connection) -> dict:
    """
    Cleanup stale interrupts and return summary.
    
    This is a stub - actual implementation needs:
    1. LangGraph checkpointer access
    2. User notification service
    3. Database connection
    
    Args:
        db_connection: Database connection for checkpoint queries
    
    Returns:
        dict with cleanup statistics
    """
    # Pseudo-code for actual implementation:
    """
    stale_interrupts = db.query('''
        SELECT thread_id, user_id, query, interrupted_at, status
        FROM checkpoints
        WHERE status = 'interrupted'
        AND interrupted_at < NOW() - INTERVAL '{INTERRUPT_TTL_HOURS} hours'
    ''')
    
    canceled_count = 0
    for run in stale_interrupts:
        # Cancel run
        update_status(run.thread_id, 'expired')
        
        # Notify user
        send_notification(
            user_id=run.user_id,
            subject="Research Expired",
            message=f"Your research '{run.query}' expired after {INTERRUPT_TTL_HOURS}h. Please restart if needed.",
            link=f"/research/{run.thread_id}/restart"
        )
        
        canceled_count += 1
    
    return {
        'canceled_count': canceled_count,
        'ttl_hours': INTERRUPT_TTL_HOURS
    }
    """
    
    # Stub return
    return {
        'canceled_count': 0,
        'ttl_hours': INTERRUPT_TTL_HOURS,
        'implementation': 'stub'
    }


def add_interrupt_metadata(state: dict) -> dict:
    """Add timeout metadata when creating interrupt.
    
    Should be called when setting up interrupt in plan_gate/hitl/memo_gate.
    
    Args:
        state: LangGraph state dict
    
    Returns:
        Updated state with timeout metadata
    """
    return {
        **state,
        'interrupted_at': datetime.utcnow().isoformat(),
        'interrupt_ttl_hours': INTERRUPT_TTL_HOURS,
        'interrupt_reminded': False,
    }


def check_interrupt_status(state: dict) -> dict:
    """Check interrupt status and return metadata for UI.
    
    Args:
        state: LangGraph state dict with interrupt metadata
    
    Returns:
        Status dict for UI display
    """
    if 'interrupted_at' not in state:
        return {'status': 'active', 'message': 'Run is active'}
    
    interrupted_at = datetime.fromisoformat(state['interrupted_at'])
    timeout_info = explain_timeout(interrupted_at)
    
    if timeout_info['expired']:
        return {
            'status': 'expired',
            'age_hours': timeout_info['age_hours'],
            'message': timeout_info['message'],
            'can_restart': True
        }
    elif should_send_reminder(interrupted_at, state.get('interrupt_reminded', False)):
        return {
            'status': 'pending_reminder',
            'age_hours': timeout_info['age_hours'],
            'hours_remaining': timeout_info['hours_remaining'],
            'message': 'Reminder needed',
            'should_notify': True
        }
    else:
        return {
            'status': 'pending',
            'age_hours': timeout_info['age_hours'],
            'hours_remaining': timeout_info['hours_remaining'],
            'message': timeout_info['message'],
            'can_resume': True
        }


# Integration points for existing nodes
def wrap_interrupt_with_timeout(interrupt_func):
    """Decorator to add timeout metadata to interrupts.
    
    Usage in plan_gate/hitl/memo_gate:
        @wrap_interrupt_with_timeout
        def plan_gate_node(state):
            # ... existing logic ...
            interrupt("approval_needed")
    """
    def wrapper(state):
        # Add timeout metadata before interrupt
        state_with_metadata = add_interrupt_metadata(state)
        # Call original interrupt
        return interrupt_func(state_with_metadata)
    return wrapper
