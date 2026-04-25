from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings
from models import ProtectionDetection, RobotsDecision


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:(?:\+?\d{1,3})?[\s().-]*)?(?:\d[\s().-]*){8,}")
CAPTCHA_PATTERNS = [
    "g-recaptcha",
    "grecaptcha",
    "recaptcha/api.js",
    "hcaptcha",
    "h-captcha",
    "data-sitekey",
    "verify you are human",
    "unusual traffic",
    "challenge page",
]
WAF_PATTERNS = [
    "cf-chl-bypass",
    "attention required",
    "cloudflare",
    "browser integrity check",
    "checking your browser before accessing",
    "request unsuccessful",
    "web application firewall",
    "access denied",
    "temporarily blocked",
    "bot detection",
    "security check",
    "request blocked",
]
LOGIN_PATTERNS = [
    "sign in",
    "sign-in",
    "log in",
    "login required",
    "please log in",
    "please sign in",
    "account required",
    "authentication required",
]
PAYWALL_PATTERNS = [
    "subscribe to continue",
    "premium content",
    "members-only",
    "members only",
    "subscriber only",
    "become a member",
    "continue reading",
]
SOCIAL_HOSTS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
    "yelp.com",
}


class DomainLimitExceeded(Exception):
    pass


class RobotsDisallowed(Exception):
    pass


class RedirectLimitExceeded(Exception):
    pass


@dataclass(slots=True)
class FetchResult:
    response: httpx.Response
    elapsed: float


def get_logger(level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return logging.getLogger("website_prospecting_bot")


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not urlparse(raw).scheme:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, "", "", ""))


def normalize_domain(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    return urlparse(normalized).netloc.lower()


def is_internal_link(base_url: str, candidate_url: str) -> bool:
    return normalize_domain(base_url) == normalize_domain(candidate_url)


def extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(EMAIL_RE.findall(text or "")))


def extract_phone_numbers(text: str) -> list[str]:
    phones = []
    for match in PHONE_RE.findall(text or ""):
        compact = normalize_whitespace(match)
        digits = re.sub(r"\D", "", compact)
        if len(digits) >= 8:
            phones.append(compact)
    return list(dict.fromkeys(phones))


def detect_blocked_page(status_code: Optional[int], body: str, headers: Optional[dict[str, str]] = None) -> bool:
    return classify_page_protection(status_code, body, headers).audit_status == "blocked_or_challenged"


def classify_page_protection(
    status_code: Optional[int],
    body: str,
    headers: Optional[dict[str, str]] = None,
    final_url: Optional[str] = None,
) -> ProtectionDetection:
    lowered = (body or "").lower()
    headers = headers or {}
    server = headers.get("server", "").lower()
    location = headers.get("location", "").lower()
    final_location = (final_url or "").lower()

    if status_code in {403, 429}:
        reason = "http_403_forbidden" if status_code == 403 else "http_429_rate_limited"
        return ProtectionDetection(blocked=True, reason=reason, audit_status="blocked_or_challenged")

    if any(pattern in lowered for pattern in CAPTCHA_PATTERNS):
        return ProtectionDetection(blocked=True, reason="captcha_or_human_verification", audit_status="blocked_or_challenged")

    if "cloudflare" in server or any(pattern in lowered for pattern in WAF_PATTERNS):
        return ProtectionDetection(blocked=True, reason="cloudflare_or_waf_challenge", audit_status="blocked_or_challenged")

    if _looks_like_auth_path(location) or _looks_like_auth_path(final_location):
        return ProtectionDetection(blocked=True, reason="auth_redirect_or_login_wall", audit_status="login_required")

    soup = BeautifulSoup(body or "", "html.parser")
    password_inputs = soup.find_all("input", attrs={"type": "password"})
    login_forms = 0
    for form in soup.find_all("form"):
        form_text = normalize_whitespace(form.get_text(" ", strip=True)).lower()
        action = (form.get("action") or "").lower()
        if any(pattern in form_text for pattern in LOGIN_PATTERNS) or any(pattern in action for pattern in ["login", "signin", "sign-in", "auth"]):
            login_forms += 1
    if password_inputs and login_forms:
        return ProtectionDetection(blocked=True, reason="login_form_required", audit_status="login_required")
    if any(pattern in lowered for pattern in LOGIN_PATTERNS):
        return ProtectionDetection(blocked=True, reason="login_wall_text_detected", audit_status="login_required")

    if any(pattern in lowered for pattern in PAYWALL_PATTERNS):
        return ProtectionDetection(blocked=True, reason="paywall_or_membership_required", audit_status="paywalled")

    return ProtectionDetection(blocked=False, reason=None, audit_status="success")


def _looks_like_auth_path(url_or_path: str) -> bool:
    if not url_or_path:
        return False
    parsed = urlparse(url_or_path)
    path = parsed.path or url_or_path
    query = parsed.query
    haystack = f"{path}?{query}".lower()
    auth_tokens = [
        "/login",
        "/log-in",
        "/signin",
        "/sign-in",
        "/account/login",
        "/auth/",
        "next=/login",
        "redirect=/login",
        "returnto=/login",
    ]
    return any(token in haystack for token in auth_tokens)


def random_delay(settings: Settings) -> None:
    time.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))


def get_text_excerpt(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return normalize_whitespace(soup.get_text(" ", strip=True))


def parse_retry_after(headers: dict[str, str]) -> Optional[float]:
    value = headers.get("retry-after")
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        return max((dt.timestamp() - time.time()), 0.0)
    except Exception:
        return None


class RobotsCache:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._cache: dict[str, RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout,
            follow_redirects=True,
            max_redirects=settings.max_redirects,
        )

    def close(self) -> None:
        self._client.close()

    def is_allowed(self, url: str) -> RobotsDecision:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return RobotsDecision(allowed=False, reason="invalid_url")
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = self._cache.get(robots_url)
        if parser is None:
            parser = RobotFileParser()
            try:
                response = self._client.get(robots_url)
                if response.status_code >= 400:
                    parser.parse([])
                else:
                    parser.parse(response.text.splitlines())
            except Exception as exc:
                self.logger.debug("robots.txt unavailable for %s: %s", robots_url, exc)
                parser.parse([])
            self._cache[robots_url] = parser
        allowed = parser.can_fetch(self.settings.user_agent, url)
        return RobotsDecision(allowed=allowed, reason="allowed" if allowed else "disallowed_by_robots")


class PoliteHttpClient:
    def __init__(self, settings: Settings, logger: logging.Logger, robots_cache: Optional[RobotsCache] = None) -> None:
        self.settings = settings
        self.logger = logger
        self.robots_cache = robots_cache or RobotsCache(settings, logger)
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout,
            follow_redirects=True,
            max_redirects=settings.max_redirects,
        )
        self._last_request_at: dict[str, float] = {}
        self._request_counts: dict[str, int] = {}

    def close(self) -> None:
        self.client.close()
        self.robots_cache.close()

    def _throttle(self, url: str) -> None:
        domain = normalize_domain(url)
        count = self._request_counts.get(domain, 0)
        if count >= self.settings.max_requests_per_domain:
            raise DomainLimitExceeded(f"Max requests reached for {domain}")
        last = self._last_request_at.get(domain, 0.0)
        minimum_gap = self.settings.min_delay_seconds
        elapsed = time.time() - last
        if elapsed < minimum_gap:
            time.sleep(minimum_gap - elapsed)
        random_delay(self.settings)
        self._request_counts[domain] = count + 1
        self._last_request_at[domain] = time.time()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _request_once(self, method: str, url: str, **kwargs) -> FetchResult:
        started = time.perf_counter()
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.TooManyRedirects as exc:
            raise RedirectLimitExceeded(str(exc)) from exc
        elapsed = time.perf_counter() - started
        if response.status_code == 429:
            wait_for = parse_retry_after(dict(response.headers))
            if wait_for:
                time.sleep(wait_for)
        return FetchResult(response=response, elapsed=elapsed)

    def request(self, method: str, url: str, respect_robots: bool = True, **kwargs) -> FetchResult:
        if respect_robots:
            decision = self.robots_cache.is_allowed(url)
            if not decision.allowed:
                raise RobotsDisallowed(decision.reason)
        self._throttle(url)
        return self._request_once(method, url, **kwargs)

    def get(self, url: str, respect_robots: bool = True, **kwargs) -> FetchResult:
        return self.request("GET", url, respect_robots=respect_robots, **kwargs)

    def head(self, url: str, respect_robots: bool = True, **kwargs) -> FetchResult:
        return self.request("HEAD", url, respect_robots=respect_robots, **kwargs)


def safe_join(base_url: str, maybe_relative: str) -> str:
    if not maybe_relative:
        return ""
    return normalize_url(urljoin(base_url, maybe_relative))


def looks_like_business_domain(url: str) -> bool:
    domain = normalize_domain(url)
    if not domain:
        return False
    return not any(domain == host or domain.endswith(f".{host}") for host in SOCIAL_HOSTS)
