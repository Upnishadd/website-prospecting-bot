from datetime import datetime, timezone

from models import WebsiteAudit
from scorer import score_audit
from utils import normalize_domain, normalize_url


def test_score_calculation_penalties_stack_and_floor():
    audit = WebsiteAudit(
        business_domain="example.com",
        checked_at=datetime.now(timezone.utc),
        unreachable=True,
        https_enabled=False,
        load_time_seconds=5.2,
        mobile_viewport=False,
        missing_title=True,
        missing_meta_description=True,
        broken_images_count=8,
        broken_internal_links_count=4,
        has_contact_form=False,
        has_mailto=False,
        has_phone_link=False,
    )

    score, issues = score_audit(audit)

    assert score == 0
    assert any("HTTPS" in issue for issue in issues)
    assert any("broken image" in issue.lower() for issue in issues)


def test_url_normalization_removes_www_and_trailing_slash():
    assert normalize_url("www.Example.com/about/") == "https://example.com/about"
    assert normalize_domain("http://www.Example.com/") == "example.com"


def test_protected_page_status_keeps_score_neutral():
    audit = WebsiteAudit(
        business_domain="example.com",
        audit_status="blocked_or_challenged",
        blocked_reason="captcha_or_human_verification",
    )

    score, issues = score_audit(audit)

    assert score == 100
    assert issues == ["captcha_or_human_verification"]
