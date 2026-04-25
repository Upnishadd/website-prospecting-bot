from __future__ import annotations

import logging
import socket
import ssl
from datetime import datetime, timezone
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
        audit = WebsiteAudit(business_domain=business.normalized_domain, checked_at=datetime.now(timezone.utc))
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

        parsed = parse_page_features(html, str(response.url))
        audit.mobile_viewport = parsed.mobile_viewport
        audit.missing_title = not bool(parsed.title)
        audit.missing_meta_description = not bool(parsed.meta_description)
        audit.has_contact_form = parsed.has_contact_form
        audit.has_mailto = parsed.has_mailto
        audit.has_phone_link = parsed.has_phone_link
        audit.outdated_design_signals = parsed.outdated_design_signals
        asset_budget = max(0, self.settings.max_asset_checks)
        image_budget = min(len(parsed.image_links), (asset_budget + 1) // 2)
        internal_link_budget = max(0, asset_budget - image_budget)

        audit.broken_images_count = self._count_broken_links(
            parsed.image_links[:image_budget],
            fallback_to_get=False,
        )
        audit.broken_internal_links_count = self._count_broken_links(
            parsed.internal_links[:internal_link_budget],
            fallback_to_get=True,
        )

        if primary_url.startswith("http://") and not audit.https_enabled:
            audit.notes.append("Site appears to work only over HTTP")

        return audit

    def _count_broken_links(self, urls: list[str], fallback_to_get: bool) -> int:
        broken = 0
        for url in urls:
            is_broken = self._is_broken_link(url, fallback_to_get=fallback_to_get)
            if is_broken is True:
                broken += 1
        return broken

    def _is_broken_link(self, url: str, fallback_to_get: bool) -> bool | None:
        try:
            fetch = self.client.head(url)
        except (RobotsDisallowed, DomainLimitExceeded, RedirectLimitExceeded, httpx.TimeoutException) as exc:
            self.logger.debug("Skipping link check for %s: %s", url, exc)
            return None
        except httpx.HTTPError as exc:
            self.logger.debug("HEAD link check failed for %s: %s", url, exc)
            return self._is_broken_link_with_get(url) if fallback_to_get else None

        status = fetch.response.status_code
        protection = classify_page_protection(
            status,
            fetch.response.text,
            dict(fetch.response.headers),
            final_url=str(fetch.response.url),
        )
        if protection.blocked:
            self.logger.debug("Skipping protected link check result for %s: %s", url, protection.reason)
            return None
        if status in {405, 501}:
            return self._is_broken_link_with_get(url) if fallback_to_get else None
        return status >= 400

    def _is_broken_link_with_get(self, url: str) -> bool | None:
        try:
            fetch = self.client.get(url)
        except (RobotsDisallowed, DomainLimitExceeded, RedirectLimitExceeded, httpx.TimeoutException) as exc:
            self.logger.debug("Skipping GET fallback link check for %s: %s", url, exc)
            return None
        except httpx.HTTPError as exc:
            self.logger.debug("GET fallback link check failed for %s: %s", url, exc)
            return None

        status = fetch.response.status_code
        protection = classify_page_protection(
            status,
            fetch.response.text,
            dict(fetch.response.headers),
            final_url=str(fetch.response.url),
        )
        if protection.blocked:
            self.logger.debug("Skipping protected GET fallback result for %s: %s", url, protection.reason)
            return None
        return status >= 400

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
