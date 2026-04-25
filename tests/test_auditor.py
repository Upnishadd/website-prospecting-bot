from auditor import parse_page_features
from utils import classify_page_protection, detect_blocked_page


def test_parse_page_features_extracts_expected_signals():
    html = """
    <html>
      <head>
        <title>Sample Site</title>
        <meta name="description" content="Example description">
        <meta name="viewport" content="width=device-width, initial-scale=1">
      </head>
      <body>
        <form action="/contact"><input name="email"></form>
        <a href="/services">Services</a>
        <a href="mailto:hello@example.com">Email</a>
        <a href="tel:+61123456789">Call</a>
        <img src="/logo.png">
      </body>
    </html>
    """

    parsed = parse_page_features(html, "https://example.com")

    assert parsed.title == "Sample Site"
    assert parsed.meta_description == "Example description"
    assert parsed.mobile_viewport is True
    assert parsed.has_contact_form is True
    assert parsed.has_mailto is True
    assert parsed.has_phone_link is True
    assert "https://example.com/services" in parsed.internal_links
    assert "https://example.com/logo.png" in parsed.image_links


def test_blocked_page_detection_handles_status_and_cloudflare_content():
    assert detect_blocked_page(403, "", {}) is True
    assert detect_blocked_page(200, "Attention Required! Cloudflare", {"server": "cloudflare"}) is True
    assert detect_blocked_page(200, "<html><body>Normal page with sign in link</body></html>", {"server": "cloudflare"}) is False
    assert detect_blocked_page(200, "<html>normal page</html>", {"server": "nginx"}) is False


def test_classify_page_protection_detects_captcha_login_and_paywall():
    captcha = classify_page_protection(200, '<div class="g-recaptcha">Verify you are human</div>', {}, "https://example.com")
    login = classify_page_protection(
        200,
        '<form action="/login"><input type="password"><button>Sign in</button></form>',
        {},
        "https://example.com/login",
    )
    paywall = classify_page_protection(200, "<div>Subscribe to continue reading this premium content</div>", {}, "https://example.com/article")

    assert captcha.audit_status == "blocked_or_challenged"
    assert captcha.reason == "captcha_or_human_verification"
    assert login.audit_status == "login_required"
    assert login.reason in {"login_form_required", "auth_redirect_or_login_wall", "login_wall_text_detected"}
    assert paywall.audit_status == "paywalled"
    assert paywall.reason == "paywall_or_membership_required"


def test_classify_page_protection_detects_search_interstitial_on_http_202():
    interstitial = classify_page_protection(
        202,
        "<html><body>Sorry, but your request looks automated. Prove you are human.</body></html>",
        {"server": "nginx"},
        "https://html.duckduckgo.com/html/?q=dentists+sydney",
    )

    assert interstitial.audit_status == "blocked_or_challenged"
    assert interstitial.reason == "challenge_or_interstitial_page"
