from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup

from config import Settings
from models import Business, SearchResult
from utils import (
    PoliteHttpClient,
    classify_page_protection,
    extract_emails,
    extract_phone_numbers,
    get_text_excerpt,
    looks_like_business_domain,
    normalize_domain,
    normalize_name,
    normalize_url,
)


class BusinessScraper:
    """Conservative search and scrape flow.

    This intentionally avoids aggressive crawling. It uses slow HTML search requests,
    checks robots where practical through the shared client, and stops on blocked pages.
    """

    def __init__(self, settings: Settings, client: PoliteHttpClient, logger: logging.Logger) -> None:
        self.settings = settings
        self.client = client
        self.logger = logger

    def discover_businesses(self, niche: str, location: str, max_results: int) -> list[Business]:
        return self.discover_businesses_with_seen_domains(
            niche=niche,
            location=location,
            max_results=max_results,
            excluded_domains=set(),
        )

    def discover_businesses_with_seen_domains(
        self,
        niche: str,
        location: str,
        max_results: int,
        excluded_domains: set[str],
    ) -> list[Business]:
        businesses: list[Business] = []
        seen_domains: set[str] = set()

        candidate_limit = max(max_results + 10, max_results * 3)
        for result in self.search(niche, location, max_results=candidate_limit):
            if len(businesses) >= max_results:
                break

            try:
                business = self.extract_business_from_site(result, location)
            except Exception as exc:
                self.logger.debug("Skipping candidate %s: %s", result.url, exc)
                continue

            if (
                not business
                or business.normalized_domain in seen_domains
                or business.normalized_domain in excluded_domains
            ):
                continue

            seen_domains.add(business.normalized_domain)
            businesses.append(business)

        return businesses

    def search(self, niche: str, location: str, max_results: int) -> list[SearchResult]:
        if self.settings.search_provider != "duckduckgo_html":
            raise ValueError(f"Unsupported SEARCH_PROVIDER: {self.settings.search_provider}")

        results: list[SearchResult] = []
        for page in range(self.settings.max_search_pages):
            if len(results) >= max_results:
                break

            params = {
                "q": f"{niche} {location} official website",
                "s": str(page * 30),
            }
            url = f"{self.settings.search_base_url}?{urlencode(params)}"
            fetch = self.client.get(url, respect_robots=False)
            protection = classify_page_protection(
                fetch.response.status_code,
                fetch.response.text,
                dict(fetch.response.headers),
                final_url=str(fetch.response.url),
            )
            if protection.blocked:
                self.logger.warning("Search provider blocked or challenged the request on page %s; stopping search", page + 1)
                break
            soup = BeautifulSoup(fetch.response.text, "html.parser")

            result_anchors = soup.select(".result__title a.result__a, a.result__a")
            for anchor in result_anchors:
                href = anchor.get("href") or ""
                resolved = self._clean_search_link(href)
                title = anchor.get_text(" ", strip=True)
                if not resolved or not title or not looks_like_business_domain(resolved):
                    continue
                results.append(SearchResult(title=title, url=resolved, source_url=url))
                if len(results) >= max_results:
                    break

        deduped = dict.fromkeys((result.url, result.title, result.source_url) for result in results)
        return [SearchResult(title=title, url=url, source_url=source_url) for url, title, source_url in deduped]

    def _clean_search_link(self, href: str) -> str:
        if not href:
            return ""
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return ""
        if "duckduckgo.com" in parsed.netloc:
            uddg = parse_qs(parsed.query).get("uddg")
            if uddg:
                destination = urlparse(uddg[0])
                if destination.scheme and destination.scheme not in {"http", "https"}:
                    return ""
                return normalize_url(uddg[0])
        if href.startswith("//"):
            href = f"https:{href}"
        if href.startswith("/"):
            return ""
        return normalize_url(href)

    def extract_business_from_site(self, result: SearchResult, location: str) -> Business | None:
        fetch = self.client.get(result.url)
        html = fetch.response.text
        if fetch.response.status_code >= 400:
            return None
        protection = classify_page_protection(
            fetch.response.status_code,
            html,
            dict(fetch.response.headers),
            final_url=str(fetch.response.url),
        )
        if protection.blocked:
            return None
        excerpt = get_text_excerpt(html)
        emails = extract_emails(html)
        phones = extract_phone_numbers(excerpt)

        soup = BeautifulSoup(html, "html.parser")
        schema_data = self._extract_jsonld_business(soup)
        og_site_name = soup.find("meta", property="og:site_name")
        name = (
            schema_data.get("name")
            or (og_site_name.get("content") if og_site_name else None)
            or (soup.title.string if soup.title and soup.title.string else None)
        )
        name = (name or result.title).split("|")[0].split("-")[0].strip()

        business_url = normalize_url(schema_data.get("url") or str(fetch.response.url))
        domain = normalize_domain(business_url)
        if not domain:
            return None

        email = schema_data.get("email") or (emails[0] if emails else None)
        phone = schema_data.get("telephone") or (phones[0] if phones else None)

        return Business(
            name=name,
            location=location,
            website_url=business_url,
            phone=phone,
            email=email,
            source_url=result.source_url or result.url,
            normalized_domain=domain,
            normalized_name=normalize_name(name),
        )

    def _extract_jsonld_business(self, soup: BeautifulSoup) -> dict[str, str]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for item in self._iter_jsonld_nodes(parsed):
                types = item.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(value in {"LocalBusiness", "Organization", "ProfessionalService"} for value in types):
                    return {
                        "name": item.get("name", ""),
                        "url": item.get("url", ""),
                        "email": item.get("email", ""),
                        "telephone": item.get("telephone", ""),
                    }
        return {}

    def _iter_jsonld_nodes(self, payload: object):
        if isinstance(payload, dict):
            if "@graph" in payload and isinstance(payload["@graph"], list):
                for node in payload["@graph"]:
                    if isinstance(node, dict):
                        yield node
            else:
                yield payload
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    yield item
