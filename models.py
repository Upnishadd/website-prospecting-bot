from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


AuditStatus = Literal[
    "success",
    "unreachable",
    "blocked_or_challenged",
    "login_required",
    "paywalled",
    "timeout",
    "error",
]


class Business(BaseModel):
    name: str
    location: str
    website_url: str
    phone: Optional[str] = None
    email: Optional[str] = None
    source_url: str
    normalized_domain: str
    normalized_name: str


class WebsiteAudit(BaseModel):
    business_domain: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    audit_status: AuditStatus = "success"
    blocked_reason: Optional[str] = None
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    https_enabled: bool = False
    ssl_valid: bool = False
    load_time_seconds: Optional[float] = None
    mobile_viewport: bool = False
    missing_title: bool = False
    missing_meta_description: bool = False
    broken_images_count: int = 0
    broken_internal_links_count: int = 0
    has_contact_form: bool = False
    has_mailto: bool = False
    has_phone_link: bool = False
    outdated_design_signals: list[str] = Field(default_factory=list)
    unreachable: bool = False
    blocked_or_challenged: bool = False
    notes: list[str] = Field(default_factory=list)
    score: int = 100
    issue_summary: list[str] = Field(default_factory=list)


class OutreachDraft(BaseModel):
    subject: str
    body: str
    recipient_email: Optional[str] = None
    send_status: str = "draft"
    sent_at: Optional[datetime] = None
    send_error: Optional[str] = None


class ExportRow(BaseModel):
    business_name: str
    location: str
    website_url: str
    phone: Optional[str] = None
    email: Optional[str] = None
    source_url: str
    audit_status: AuditStatus
    blocked_reason: Optional[str] = None
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    https_enabled: bool
    ssl_valid: bool
    load_time_seconds: Optional[float] = None
    mobile_viewport: bool
    missing_title: bool
    missing_meta_description: bool
    broken_images_count: int
    broken_internal_links_count: int
    has_contact_form: bool
    has_mailto: bool
    has_phone_link: bool
    outdated_design_signals: list[str]
    unreachable: bool
    blocked_or_challenged: bool
    score: int
    issue_summary: list[str]
    outreach_subject: Optional[str] = None
    outreach_body: Optional[str] = None


class SearchResult(BaseModel):
    title: str
    url: str
    source_url: Optional[str] = None


class NicheCityQueueItem(BaseModel):
    id: int
    niche_name: str
    city: str
    country: str
    query: str
    active: bool = True
    priority: int = 100
    target_leads_per_pack: int = 25
    audit_limit: int = 10
    last_run_at: Optional[datetime] = None
    runs_count: int = 0
    is_exhausted: bool = False
    created_at: datetime


class SummaryStats(BaseModel):
    total_checked: int = 0
    bad_websites_found: int = 0
    unreachable_sites: int = 0
    missing_https: int = 0
    slow_sites: int = 0
    csv_path: Optional[str] = None


class RobotsDecision(BaseModel):
    allowed: bool
    reason: str


class ProtectionDetection(BaseModel):
    blocked: bool = False
    reason: Optional[str] = None
    audit_status: AuditStatus = "success"


class ContactSignals(BaseModel):
    has_contact_form: bool = False
    has_mailto: bool = False
    has_phone_link: bool = False


class PageParseResult(BaseModel):
    title: Optional[str] = None
    meta_description: Optional[str] = None
    mobile_viewport: bool = False
    has_contact_form: bool = False
    has_mailto: bool = False
    has_phone_link: bool = False
    internal_links: list[str] = Field(default_factory=list)
    image_links: list[str] = Field(default_factory=list)
    outdated_design_signals: list[str] = Field(default_factory=list)

    @field_validator("internal_links", "image_links")
    @classmethod
    def dedupe_links(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))
