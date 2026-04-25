from __future__ import annotations

import logging
import socket
import ssl
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import Settings
from models import Business, PageParseResult, WebsiteAudit
from utils import (
    DomainLimitExceeded,
    PoliteHttpClient,
    RedirectLimitExceeded,
    RobotsDisallowed,
    classify_page_protection,
    is_internal_link,
    normalize_domain,
    normalize_url,
    safe_join,
)


def parse_page_features(html: str, base_url: str) -> PageParseResult:
    soup = BeautifulSoup(html or "", "html.parser")
    title_tag = soup.find("title")
    meta_description = soup.find("meta", attrs={"name": "description"})
    viewport = soup.find("meta", attrs={"name": "viewport"})

    has_contact_form = False
    has_mailto = False
    has_phone_link = False
    internal_links: list[str] = []
    image_links: list[str] = []
    outdated_signals: list[str] = []

    if soup.find(["marquee", "center", "font"]):
        outdated_signals.append("Deprecated HTML tags detected")
    if soup.find("table") and len(soup.find_all("table")) >= 3:
        outdated_signals.append("Heavy table-based layout detected")
    if "wp-content/themes/" in html.lower() and "jquery-1." in html.lower():
        outdated_signals.append("Older frontend asset patterns detected")

    for form in soup.find_all("form"):
        text = form.get_text(" ", strip=True).lower()
        action = (form.get("action") or "").lower()
        field_names = " ".join(
            (field.get("name") or "") + " " + (field.get("id") or "")
            for field in form.find_all(["input", "textarea", "select"])
        ).lower()
        has_password = bool(form.find("input", attrs={"type": "password"}))
        looks_like_contact = any(
            marker in text or marker in action or marker in field_names
            for marker in ["contact", "message", "enquiry", "inquiry", "quote", "support", "callback"]
        )
        if looks_like_contact and not has_password:
            has_contact_form = True
            break

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            has_mailto = True
            continue
        if lowered.startswith("tel:"):
            has_phone_link = True
            continue
        absolute = safe_join(base_url, href)
        if absolute and is_internal_link(base_url, absolute):
            internal_links.append(absolute)

    for image in soup.find_all("img", src=True):
        src = safe_join(base_url, image.get("src") or "")
        if src:
            image_links.append(src)

    return PageParseResult(
        title=title_tag.get_text(strip=True) if title_tag else None,
        meta_description=meta_description.get("content", "").strip() if meta_description else None,
        mobile_viewport=bool(viewport),
        has_contact_form=has_contact_form,
        has_mailto=has_mailto,
        has_phone_link=has_phone_link,
        internal_links=internal_links,
        image_links=image_links,
        outdated_design_signals=outdated_signals,
    )


class WebsiteAuditor:
    def __init__(self, settings: Settings, client: PoliteHttpClient, logger: logging.Logger) -> None:
        self.settings = settings
        self.client = client
        self.logger = logger

    def audit(self, business: Business) -> WebsiteAudit:
        audit = WebsiteAudit(business_domain=business.normalized_domain, checked_at=datetime.utcnow())
        target_url = normalize_url(business.website_url)
        https_url = self._as_https(target_url)
        http_url = self._as_http(target_url)

        primary_url = https_url
        fallback_url = http_url if http_url != https_url else None

        try:
            fetch = self.client.get(primary_url)
        except RobotsDisallowed as exc:
            audit.audit_status = "blocked_or_challenged"
            audit.blocked_or_challenged = True
            audit.blocked_reason = "disallowed_by_robots"
            audit.notes.append(str(exc))
            return audit
        except DomainLimitExceeded as exc:
            audit.audit_status = "blocked_or_challenged"
            audit.blocked_or_challenged = True
            audit.blocked_reason = "domain_rate_limit_reached"
            audit.notes.append(str(exc))
            return audit
        except RedirectLimitExceeded as exc:
            audit.audit_status = "error"
            audit.blocked_reason = "max_redirects_exceeded"
            audit.notes.append(str(exc))
            return audit
        except httpx.TimeoutException as exc:
            audit.audit_status = "timeout"
            audit.blocked_reason = "request_timeout"
            audit.notes.append(str(exc))
            return audit
        except Exception as primary_exc:
            self.logger.debug("HTTPS fetch failed for %s: %s", business.website_url, primary_exc)
            if fallback_url is None:
                audit.audit_status = "error"
                audit.unreachable = True
                audit.blocked_reason = "request_error"
                audit.notes.append(str(primary_exc))
                return audit
            try:
                fetch = self.client.get(fallback_url)
                primary_url = fallback_url
            except RobotsDisallowed as exc:
                audit.audit_status = "blocked_or_challenged"
                audit.blocked_or_challenged = True
                audit.blocked_reason = "disallowed_by_robots"
                audit.notes.append(str(exc))
                return audit
            except DomainLimitExceeded as exc:
                audit.audit_status = "blocked_or_challenged"
                audit.blocked_or_challenged = True
                audit.blocked_reason = "domain_rate_limit_reached"
                audit.notes.append(str(exc))
                return audit
            except RedirectLimitExceeded as exc:
                audit.audit_status = "error"
                audit.blocked_reason = "max_redirects_exceeded"
                audit.notes.append(str(exc))
                return audit
            except httpx.TimeoutException as exc:
                audit.audit_status = "timeout"
                audit.blocked_reason = "request_timeout"
                audit.notes.append(str(exc))
                return audit
            except Exception as fallback_exc:
                audit.audit_status = "unreachable"
                audit.unreachable = True
                audit.blocked_reason = "request_error"
                audit.notes.append(str(fallback_exc))
                return audit

        response = fetch.response
        html = response.text
        audit.final_url = str(response.url)
        audit.http_status = response.status_code
        audit.load_time_seconds = round(fetch.elapsed, 3)
        audit.https_enabled = urlparse(str(response.url)).scheme.lower() == "https"
        protection = classify_page_protection(
            response.status_code,
            html,
            dict(response.headers),
            final_url=str(response.url),
        )
        if protection.blocked:
            audit.audit_status = protection.audit_status
            audit.blocked_reason = protection.reason
            audit.blocked_or_challenged = protection.audit_status == "blocked_or_challenged"
            audit.notes.append(protection.reason or "protected_page_detected")
            return audit

        if response.status_code >= 400:
            audit.audit_status = "unreachable"
            audit.unreachable = True
            audit.blocked_reason = f"http_status_{response.status_code}"
            audit.notes.append(f"Bad HTTP status {response.status_code}")
            return audit

        audit.audit_status = "success"
        audit.ssl_valid = self._check_ssl_validity(normalize_domain(audit.final_url)) if audit.https_enabled else False

        if self._should_try_playwright(html):
            rendered = self._render_with_playwright(str(response.url))
            if rendered:
                html = rendered
                protection = classify_page_protection(
                    response.status_code,
                    html,
                    dict(response.headers),
                    final_url=str(response.url),
                )
                if protection.blocked:
                    audit.audit_status = protection.audit_status
                    audit.blocked_reason = protection.reason
                    audit.blocked_or_challenged = protection.audit_status == "blocked_or_challenged"
                    audit.notes.append(protection.reason or "protected_page_detected")
                    return audit

        parsed = parse_page_features(html, str(response.url))
        audit.mobile_viewport = parsed.mobile_viewport
        audit.missing_title = not bool(parsed.title)
        audit.missing_meta_description = not bool(parsed.meta_description)
        audit.has_contact_form = parsed.has_contact_form
        audit.has_mailto = parsed.has_mailto
        audit.has_phone_link = parsed.has_phone_link
        audit.outdated_design_signals = parsed.outdated_design_signals
        audit.broken_images_count = self._count_broken_links(
            parsed.image_links[: self.settings.max_asset_checks],
            fallback_to_get=False,
        )
        audit.broken_internal_links_count = self._count_broken_links(
            parsed.internal_links[: self.settings.max_asset_checks],
            fallback_to_get=True,
        )

        if primary_url.startswith("http://") and not audit.https_enabled:
            audit.notes.append("Site appears to work only over HTTP")

        return audit

    def _count_broken_links(self, urls: list[str], fallback_to_get: bool) -> int:
        broken = 0
        for url in urls:
            try:
                fetch = self.client.head(url)
                status = fetch.response.status_code
                protection = classify_page_protection(status, fetch.response.text, dict(fetch.response.headers), final_url=str(fetch.response.url))
                if status >= 400 or protection.blocked:
                    broken += 1
            except (RobotsDisallowed, DomainLimitExceeded, RedirectLimitExceeded, httpx.TimeoutException):
                broken += 1
            except Exception:
                if not fallback_to_get:
                    broken += 1
                    continue
                try:
                    fetch = self.client.get(url)
                    status = fetch.response.status_code
                    protection = classify_page_protection(status, fetch.response.text, dict(fetch.response.headers), final_url=str(fetch.response.url))
                    if status >= 400 or protection.blocked:
                        broken += 1
                except Exception:
                    broken += 1
        return broken

    def _as_https(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            return f"https://{url}"
        return parsed._replace(scheme="https").geturl()

    def _as_http(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            return f"http://{url}"
        return parsed._replace(scheme="http").geturl()

    def _should_try_playwright(self, html: str) -> bool:
        if not self.settings.enable_playwright:
            return False
        soup = BeautifulSoup(html or "", "html.parser")
        body_text = soup.get_text(" ", strip=True)
        script_count = len(soup.find_all("script"))
        return len(body_text) < 100 and script_count >= 5

    def _render_with_playwright(self, url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.logger.debug("Playwright unavailable: %s", exc)
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.settings.user_agent)
                page.goto(url, wait_until="networkidle", timeout=int(self.settings.request_timeout * 1000))
                html = page.content()
                browser.close()
                return html
        except Exception as exc:
            self.logger.debug("Playwright render failed for %s: %s", url, exc)
            return None

    def _check_ssl_validity(self, hostname: str) -> bool:
        if not hostname:
            return False
        context = ssl.create_default_context()
        try:
            with socket.create_connection((hostname, 443), timeout=self.settings.request_timeout) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as secure_sock:
                    secure_sock.getpeercert()
                    return True
        except Exception as exc:
            self.logger.debug("SSL check failed for %s: %s", hostname, exc)
            return False
