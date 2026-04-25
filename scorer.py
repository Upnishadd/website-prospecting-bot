from __future__ import annotations

from models import WebsiteAudit


def score_audit(audit: WebsiteAudit) -> tuple[int, list[str]]:
    if audit.audit_status in {"blocked_or_challenged", "login_required", "paywalled", "timeout", "error"}:
        detail = audit.blocked_reason or "Protected or unavailable during audit; no further checks were performed"
        return 100, [detail]
    if audit.blocked_or_challenged:
        return 100, ["Blocked or challenged during audit; no further checks were performed"]

    score = 100
    issues: list[str] = []

    if audit.unreachable:
        score -= 50
        issues.append("Website appears down or unreachable")
    if not audit.https_enabled:
        score -= 20
        issues.append("HTTPS is not properly enabled")
    if audit.load_time_seconds is not None and audit.load_time_seconds > 4:
        score -= 15
        issues.append(f"Page load time is slow at {audit.load_time_seconds:.2f}s")
    if audit.missing_title:
        score -= 10
        issues.append("Page title is missing")
    if audit.missing_meta_description:
        score -= 10
        issues.append("Meta description is missing")
    if not audit.mobile_viewport:
        score -= 15
        issues.append("Mobile viewport meta tag is missing")

    if audit.broken_images_count > 0:
        penalty = min(audit.broken_images_count, 10)
        score -= penalty
        issues.append(f"{audit.broken_images_count} broken image(s) detected")

    if audit.broken_internal_links_count > 0:
        penalty = min(audit.broken_internal_links_count, 10)
        score -= penalty
        issues.append(f"{audit.broken_internal_links_count} broken internal link(s) detected")

    if not any([audit.has_contact_form, audit.has_mailto, audit.has_phone_link]):
        score -= 10
        issues.append("No clear contact form or contact link was detected")

    final_score = max(score, 0)
    return final_score, issues[:]
