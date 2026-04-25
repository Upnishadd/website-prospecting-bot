from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(slots=True)
class Settings:
    database_url: str
    supabase_url: str
    supabase_secret_key: str
    enable_email_sending: bool
    gmail_sender_email: str
    gmail_app_password: str
    email_sender_name: str
    user_agent: str
    request_timeout: float
    max_redirects: int
    min_delay_seconds: float
    max_delay_seconds: float
    max_requests_per_domain: int
    max_asset_checks: int
    max_search_pages: int
    max_outreach_score: int
    search_provider: str
    search_base_url: str
    log_level: str
    export_dir: Path
    enable_csv_export: bool


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in {None, ""} else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _as_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in {None, ""} else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if minimum is not None:
        value = max(minimum, value)
    return value


def load_settings() -> Settings:
    export_dir = BASE_DIR / (os.getenv("EXPORT_DIR") or "exports")
    export_dir.mkdir(parents=True, exist_ok=True)
    min_delay_seconds = _as_float("MIN_DELAY_SECONDS", 1.5, minimum=0.0)
    max_delay_seconds = max(min_delay_seconds, _as_float("MAX_DELAY_SECONDS", 3.5, minimum=0.0))

    return Settings(
        database_url=os.getenv("DATABASE_URL", ""),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", ""),
        enable_email_sending=_as_bool(os.getenv("ENABLE_EMAIL_SENDING", "false")),
        gmail_sender_email=os.getenv("GMAIL_SENDER_EMAIL", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
        email_sender_name=os.getenv("EMAIL_SENDER_NAME", "Website Issue Prospecting Bot"),
        user_agent=os.getenv(
            "USER_AGENT",
            "WebsiteIssueProspectingBot/1.0 (+local research; contact admin@example.com)",
        ),
        request_timeout=_as_float("REQUEST_TIMEOUT", 15.0, minimum=1.0),
        max_redirects=_as_int("MAX_REDIRECTS", 5, minimum=0),
        min_delay_seconds=min_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        max_requests_per_domain=_as_int("MAX_REQUESTS_PER_DOMAIN", 12, minimum=1),
        max_asset_checks=_as_int("MAX_ASSET_CHECKS", 8, minimum=0),
        max_search_pages=_as_int("MAX_SEARCH_PAGES", 5, minimum=1),
        max_outreach_score=_as_int("MAX_OUTREACH_SCORE", 70, minimum=0, maximum=100),
        search_provider=os.getenv("SEARCH_PROVIDER", "duckduckgo_html"),
        search_base_url=os.getenv("SEARCH_BASE_URL", "https://html.duckduckgo.com/html/"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        export_dir=export_dir,
        enable_csv_export=_as_bool(os.getenv("ENABLE_CSV_EXPORT", "true"), default=True),
    )
