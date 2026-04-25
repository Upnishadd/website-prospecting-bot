from __future__ import annotations

from models import Business, OutreachDraft, WebsiteAudit


def generate_outreach_draft(business: Business, audit: WebsiteAudit) -> OutreachDraft:
    selected_issues = _select_outreach_issues(audit)[:3]
    issue_text = _format_issue_list(selected_issues)
    subject = f"Quick website note for {business.name}"
    body = (
        f"Hi {business.name},\n\n"
        f"I noticed your website may have a few issues that could be costing enquiries. "
        f"From a quick review of {business.website_url}, I found {issue_text}.\n\n"
        "If helpful, I can send over a short plain-English summary with a few practical fixes to consider.\n\n"
        "Best,\n"
        "[Your Name]"
    )
    return OutreachDraft(subject=subject, body=body)


def _select_outreach_issues(audit: WebsiteAudit) -> list[str]:
    issue_map = [
        (audit.unreachable, "the site appears to be unreachable"),
        (not audit.https_enabled, "HTTPS does not appear to be properly enabled"),
        (
            audit.load_time_seconds is not None and audit.load_time_seconds > 4,
            f"the page looks slow to load at around {audit.load_time_seconds:.1f}s",
        ),
        (audit.missing_title, "the page title appears to be missing"),
        (audit.missing_meta_description, "the meta description appears to be missing"),
        (not audit.mobile_viewport, "the site may not be fully mobile-friendly"),
        (audit.broken_images_count > 0, f"I found {audit.broken_images_count} broken image(s)"),
        (audit.broken_internal_links_count > 0, f"I found {audit.broken_internal_links_count} broken internal link(s)"),
        (
            not any([audit.has_contact_form, audit.has_mailto, audit.has_phone_link]),
            "I could not find a clear contact form or contact link",
        ),
    ]
    selected = [message for condition, message in issue_map if condition]
    if selected:
        return selected
    return ["a few website improvements may be worth reviewing"]


def _format_issue_list(issues: list[str]) -> str:
    lowered = [issue[:1].lower() + issue[1:] if issue else issue for issue in issues]
    if len(lowered) == 1:
        return lowered[0]
    if len(lowered) == 2:
        return f"{lowered[0]} and {lowered[1]}"
    return f"{lowered[0]}, {lowered[1]}, and {lowered[2]}"
