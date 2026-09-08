"""Content sanitization to prevent prompt injection attacks.

Addresses Issue #10: No defense layer between external web content and writer LLM.

Strategy:
- Detect and neutralize common prompt injection patterns
- Remove/escape instruction-like phrases in fetched content
- Preserve legitimate technical content (code examples, command instructions)
- Log suspicious patterns for monitoring

Patterns detected:
- Direct instruction injections ("ignore previous", "disregard instructions")
- Role-play attacks ("you are now", "act as", "pretend to be")
- System prompt leaks ("repeat your instructions", "show system prompt")
- Output format manipulation ("respond only with", "output format:")
- Jailbreak attempts ("DAN mode", "developer mode")
"""

from __future__ import annotations

import re
from typing import Any


# Suspicious patterns that suggest prompt injection attempts
INJECTION_PATTERNS = [
    # Direct instruction manipulation
    (r'\bignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|commands?)', 'instruction_override'),
    (r'\bdisregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?)', 'instruction_override'),
    (r'\bforget\s+(all\s+)?(previous|prior|earlier)\s+(instructions?|context)', 'instruction_override'),
    (r'\boverride\s+(all\s+)?(previous|system)\s+(instructions?|settings?)', 'instruction_override'),
    
    # Role-play / identity manipulation
    (r'\byou\s+are\s+now\s+(a|an)\s+\w+', 'role_manipulation'),
    (r'\bact\s+as\s+(a|an)\s+\w+', 'role_manipulation'),
    (r'\bpretend\s+(to\s+be|you\s+are)\s+\w+', 'role_manipulation'),
    (r'\bassume\s+the\s+role\s+of', 'role_manipulation'),
    
    # System prompt leaks
    (r'\brepeat\s+(your|the)\s+(system\s+)?(instructions?|prompt)', 'prompt_leak'),
    (r'\bshow\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions?)', 'prompt_leak'),
    (r'\bwhat\s+(are|is)\s+your\s+(system\s+)?(instructions?|prompt)', 'prompt_leak'),
    (r'\bdisplay\s+your\s+(internal\s+)?(instructions?|rules?)', 'prompt_leak'),
    
    # Output format manipulation
    (r'\brespond\s+only\s+with\s+\w+', 'output_manipulation'),
    (r'\boutput\s+format:\s*\w+', 'output_manipulation'),
    (r'\bgenerate\s+only\s+\w+\s+(without|no)\s+\w+', 'output_manipulation'),
    
    # Jailbreak keywords
    (r'\b(DAN|developer)\s+mode\b', 'jailbreak'),
    (r'\bdo\s+anything\s+now\b', 'jailbreak'),
    (r'\bjailbreak\b', 'jailbreak'),
]

# Patterns to preserve (legitimate technical content)
ALLOWED_CONTEXTS = [
    r'```[a-z]*\n.*?```',  # Code blocks
    r'`[^`]+`',  # Inline code
    r'^\s*(#|//|/\*)',  # Comment lines
    r'https?://[^\s]+',  # URLs
]


def detect_injection_attempts(text: str) -> list[dict[str, Any]]:
    """Detect potential prompt injection patterns in text.
    
    Args:
        text: Raw text to scan
    
    Returns:
        List of detected patterns with locations and types
    """
    if not text:
        return []
    
    detections = []
    text_lower = text.lower()
    
    for pattern, attack_type in INJECTION_PATTERNS:
        matches = list(re.finditer(pattern, text_lower, re.IGNORECASE | re.MULTILINE))
        for match in matches:
            # Check if match is inside an allowed context (code block, URL, etc.)
            in_allowed_context = False
            for allowed_pattern in ALLOWED_CONTEXTS:
                if re.search(allowed_pattern, text[max(0, match.start() - 100):match.end() + 100], re.DOTALL):
                    in_allowed_context = True
                    break
            
            if not in_allowed_context:
                detections.append({
                    'type': attack_type,
                    'pattern': pattern,
                    'matched_text': match.group(0),
                    'start': match.start(),
                    'end': match.end(),
                    'context': text[max(0, match.start() - 50):match.end() + 50]
                })
    
    return detections


def sanitize_content(text: str, *, aggressive: bool = False) -> tuple[str, list[dict]]:
    """Sanitize text to remove/neutralize prompt injection attempts.
    
    Args:
        text: Raw text to sanitize
        aggressive: If True, removes sentences containing suspicious patterns.
                   If False, only neutralizes (adds warnings, escapes)
    
    Returns:
        (sanitized_text, detections_list)
    """
    if not text:
        return text, []
    
    detections = detect_injection_attempts(text)
    
    if not detections:
        return text, []
    
    sanitized = text
    
    if aggressive:
        # Remove sentences containing injection attempts
        for detection in sorted(detections, key=lambda d: d['start'], reverse=True):
            # Find sentence boundaries
            start = max(0, sanitized.rfind('.', 0, detection['start']) + 1)
            end = sanitized.find('.', detection['end'])
            if end == -1:
                end = len(sanitized)
            else:
                end += 1
            
            # Remove sentence
            sanitized = sanitized[:start].rstrip() + ' ' + sanitized[end:].lstrip()
    else:
        # Neutralize by adding warning prefix
        for detection in sorted(detections, key=lambda d: d['start'], reverse=True):
            matched_text = detection['matched_text']
            # Escape by prefixing with [QUOTED]:
            neutralized = f"[QUOTED FROM SOURCE]: \"{matched_text}\""
            sanitized = (
                sanitized[:detection['start']] +
                neutralized +
                sanitized[detection['end']:]
            )
    
    return sanitized, detections


def sanitize_evidence_item(item: dict, *, aggressive: bool = False) -> tuple[dict, list[dict]]:
    """Sanitize a single evidence dict (from enrich/scholar/search).
    
    Args:
        item: Evidence dict with 'content', 'snippet', 'summary' fields
        aggressive: Sanitization mode
    
    Returns:
        (sanitized_item, all_detections)
    """
    sanitized_item = {**item}
    all_detections = []
    
    # Sanitize text fields
    for field in ['content', 'snippet', 'summary', 'abstract']:
        if field in item and isinstance(item[field], str):
            sanitized_text, detections = sanitize_content(item[field], aggressive=aggressive)
            if detections:
                sanitized_item[field] = sanitized_text
                for det in detections:
                    det['field'] = field
                    det['url'] = item.get('url', 'unknown')
                all_detections.extend(detections)
    
    return sanitized_item, all_detections


def sanitize_evidence_list(
    evidence: list[dict],
    *,
    aggressive: bool = False,
    log_detections: bool = True
) -> tuple[list[dict], dict]:
    """Sanitize full evidence list before passing to writer.
    
    Args:
        evidence: List of evidence dicts
        aggressive: Sanitization mode
        log_detections: Whether to log detections
    
    Returns:
        (sanitized_evidence, sanitization_report)
    """
    sanitized_evidence = []
    total_detections = []
    items_sanitized = 0
    
    for item in evidence:
        sanitized_item, detections = sanitize_evidence_item(item, aggressive=aggressive)
        sanitized_evidence.append(sanitized_item)
        
        if detections:
            items_sanitized += 1
            total_detections.extend(detections)
    
    report = {
        'total_items': len(evidence),
        'items_sanitized': items_sanitized,
        'total_detections': len(total_detections),
        'detection_types': {},
        'suspicious_urls': []
    }
    
    # Aggregate statistics
    for det in total_detections:
        det_type = det['type']
        report['detection_types'][det_type] = report['detection_types'].get(det_type, 0) + 1
        
        url = det.get('url', 'unknown')
        if url not in report['suspicious_urls']:
            report['suspicious_urls'].append(url)
    
    if log_detections and total_detections:
        from app.observability.logging import event
        event('content_sanitization_triggered', {
            'items_sanitized': items_sanitized,
            'total_detections': len(total_detections),
            'detection_types': report['detection_types'],
            'suspicious_url_count': len(report['suspicious_urls']),
            'mode': 'aggressive' if aggressive else 'neutral'
        })
    
    return sanitized_evidence, report


def is_suspicious_url(url: str) -> bool:
    """Quick check if URL domain is known to host injection attempts.
    
    This is a placeholder for future integration with threat intelligence feeds.
    """
    # Placeholder - could integrate with threat DB
    suspicious_patterns = [
        r'malicious-site\.com',
        r'injection-test\.org',
    ]
    
    url_lower = url.lower()
    return any(re.search(pattern, url_lower) for pattern in suspicious_patterns)
