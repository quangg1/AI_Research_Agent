"""Tier-B audit feedback loop to close quality monitoring gap.

Addresses Issue #1: trust_bench_e2e runs post-publish but doesn't feed back
into the system. No action taken when high hallucination detected.

Strategy: Async notification + memo flagging (Option A from design doc)

Flow:
1. memo_gate approves → user receives memo
2. trust_bench_e2e runs async audit
3. If hallucination > threshold → update memo with warning + notify user
4. User sees warning banner on memo

This is non-blocking (user gets memo immediately) but transparent
(user warned if quality issues found post-hoc).
"""

from __future__ import annotations

from typing import Any


# Hallucination thresholds
HALLUCINATION_WARNING_THRESHOLD = 0.15  # 15% = show warning
HALLUCINATION_CRITICAL_THRESHOLD = 0.25  # 25% = critical warning

# Confidence thresholds (from trust_bench scoring)
MIN_HIGH_CONFIDENCE_CLAIMS = 3  # Need at least 3 HIGH confidence claims
MAX_AUTHOR_ASSUMPTION_RATIO = 0.40  # Max 40% author assumptions


def should_flag_memo(audit_result: dict) -> tuple[bool, str, str]:
    """Determine if memo should be flagged based on audit results.
    
    Args:
        audit_result: Result from trust_bench_e2e audit
    
    Returns:
        (should_flag, severity, reason)
        severity: "warning" | "critical" | "ok"
    """
    hallucination_rate = audit_result.get("hallucination_rate", 0)
    confidence_breakdown = audit_result.get("confidence_breakdown", {})
    
    high_count = confidence_breakdown.get("HIGH", 0)
    author_count = confidence_breakdown.get("author_assumption", 0)
    total_claims = sum(confidence_breakdown.values()) if confidence_breakdown else 0
    author_ratio = author_count / max(1, total_claims)
    
    # Critical: High hallucination rate
    if hallucination_rate >= HALLUCINATION_CRITICAL_THRESHOLD:
        return (
            True,
            "critical",
            f"High hallucination rate detected ({hallucination_rate:.0%}). "
            f"Some claims may not be supported by sources. Review carefully."
        )
    
    # Warning: Moderate hallucination rate
    if hallucination_rate >= HALLUCINATION_WARNING_THRESHOLD:
        return (
            True,
            "warning",
            f"Moderate hallucination rate detected ({hallucination_rate:.0%}). "
            f"Some claims may need verification."
        )
    
    # Warning: Low high-confidence claims
    if high_count < MIN_HIGH_CONFIDENCE_CLAIMS and total_claims > 0:
        return (
            True,
            "warning",
            f"Limited high-confidence claims ({high_count}). "
            f"Most claims are from secondary sources or author assumptions."
        )
    
    # Warning: Too many author assumptions
    if author_ratio > MAX_AUTHOR_ASSUMPTION_RATIO and total_claims >= 5:
        return (
            True,
            "warning",
            f"High author assumption ratio ({author_ratio:.0%}). "
            f"Many claims are inferred rather than directly cited."
        )
    
    return (False, "ok", "Audit passed")


def generate_warning_banner(severity: str, reason: str, audit_details: dict) -> dict:
    """Generate warning banner HTML/metadata for UI.
    
    Args:
        severity: "warning" | "critical"
        reason: Human-readable explanation
        audit_details: Full audit result for details modal
    
    Returns:
        dict with banner config for UI
    """
    if severity == "critical":
        return {
            "banner_type": "error",
            "icon": "⚠️",
            "title": "Quality Alert",
            "message": reason,
            "show_details_link": True,
            "audit_details": audit_details,
            "dismissible": False,  # Critical warnings can't be dismissed
        }
    elif severity == "warning":
        return {
            "banner_type": "warning",
            "icon": "⚡",
            "title": "Quality Notice",
            "message": reason,
            "show_details_link": True,
            "audit_details": audit_details,
            "dismissible": True,  # Warnings can be dismissed
        }
    else:
        return None


def process_audit_result(
    memo_id: str,
    audit_result: dict,
    *,
    update_callback: callable = None,
    notify_callback: callable = None
) -> dict:
    """Process audit result and trigger actions if needed.
    
    This is called by trust_bench_e2e after audit completes.
    
    Args:
        memo_id: Memo identifier
        audit_result: Result from trust_bench_e2e
        update_callback: Function to update memo in DB (memo_id, warning_data)
        notify_callback: Function to notify user (user_id, notification_data)
    
    Returns:
        Action summary dict
    """
    should_flag, severity, reason = should_flag_memo(audit_result)
    
    if not should_flag:
        return {
            "action": "none",
            "severity": "ok",
            "memo_id": memo_id,
            "hallucination_rate": audit_result.get("hallucination_rate", 0)
        }
    
    # Generate warning banner
    warning_banner = generate_warning_banner(severity, reason, audit_result)
    
    # Update memo in DB (if callback provided)
    if update_callback:
        update_callback(memo_id, {
            "quality_warning": warning_banner,
            "audit_result": audit_result,
            "flagged_at": "utcnow()",  # Placeholder for actual timestamp
        })
    
    # Notify user (if callback provided and severity is critical)
    if notify_callback and severity == "critical":
        notify_callback({
            "type": "quality_alert",
            "severity": severity,
            "memo_id": memo_id,
            "message": reason,
            "action_url": f"/memo/{memo_id}#quality-alert"
        })
    
    return {
        "action": "flagged",
        "severity": severity,
        "memo_id": memo_id,
        "hallucination_rate": audit_result.get("hallucination_rate", 0),
        "warning_banner": warning_banner,
        "user_notified": notify_callback is not None and severity == "critical"
    }


# Integration point for trust_bench_e2e
def integrate_with_trust_bench(trust_bench_module):
    """
    Integration pseudo-code for trust_bench_e2e.py:
    
    # At end of trust_bench_e2e audit:
    def run_audit(memo_id, memo_markdown, evidence):
        # ... existing audit logic ...
        audit_result = {
            'hallucination_rate': hallucination_rate,
            'confidence_breakdown': confidence_breakdown,
            'claims': claims_with_verdicts,
        }
        
        # NEW: Process audit result and trigger feedback
        from app.maintenance.tier_b_feedback import process_audit_result
        from app.database import update_memo_warning, notify_user
        
        action_summary = process_audit_result(
            memo_id=memo_id,
            audit_result=audit_result,
            update_callback=update_memo_warning,  # DB update function
            notify_callback=notify_user,  # Notification function
        )
        
        # Log action
        event("tier_b_audit_feedback", action_summary)
        
        return audit_result
    """
    pass


def get_memo_quality_status(memo_data: dict) -> dict:
    """Get quality status for UI display.
    
    Args:
        memo_data: Memo dict from DB (includes quality_warning if flagged)
    
    Returns:
        Status dict for UI
    """
    quality_warning = memo_data.get("quality_warning")
    
    if not quality_warning:
        return {
            "status": "ok",
            "has_warning": False
        }
    
    return {
        "status": quality_warning.get("severity", "warning"),
        "has_warning": True,
        "banner": quality_warning,
        "audit_timestamp": memo_data.get("flagged_at"),
    }
