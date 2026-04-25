from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import Settings
from models import Business, NicheCityQueueItem, OutreachDraft, WebsiteAudit

try:
    from supabase import Client, create_client
except Exception:  # pragma: no cover - optional import during static review
    Client = object  # type: ignore[assignment]
    create_client = None  # type: ignore[assignment]


class Database:
    def __init__(self, settings: Settings, schema_path: Path, logger: logging.Logger) -> None:
        self.settings = settings
        self.schema_path = schema_path
        self.logger = logger
        self.engine: Optional[Engine] = None
        self.supabase: Optional[Client] = None
        self.backend: str = "disabled"
        self.available: bool = False

        self._initialize_backend()

    def _initialize_backend(self) -> None:
        if self.settings.supabase_url and self.settings.supabase_secret_key:
            self._initialize_supabase()
            if self.available:
                return

        if self.settings.database_url:
            self._initialize_postgres()
            if self.available:
                return

        self.logger.warning("No database backend configured; continuing with CSV export only")

    def _initialize_supabase(self) -> None:
        if create_client is None:
            self.logger.error("Supabase client dependency is unavailable; continuing with CSV export only")
            return

        try:
            self.supabase = create_client(
                self.settings.supabase_url,
                self.settings.supabase_secret_key,
            )
            self.backend = "supabase"
            self.available = True
            self.logger.info("Supabase backend enabled")
        except Exception as exc:
            self.logger.error(
                "Supabase is unavailable; continuing with CSV export only (%s)",
                exc.__class__.__name__,
            )
            self.supabase = None
            self.available = False

    def _initialize_postgres(self) -> None:
        try:
            self.engine = create_engine(self.settings.database_url, future=True)
            self.backend = "postgres"
            self.available = True
            self.logger.info("PostgreSQL backend enabled")
        except Exception as exc:
            self.logger.error(
                "PostgreSQL is unavailable; continuing with CSV export only (%s)",
                exc.__class__.__name__,
            )
            self.engine = None
            self.available = False

    def ensure_schema(self) -> None:
        if not self.available:
            return
        if self.backend == "supabase":
            self.logger.info("Using Supabase-managed schema; paste schema.sql into the Supabase SQL editor if needed")
            return
        if self.engine is None:
            return

        schema_sql = self.schema_path.read_text(encoding="utf-8")
        conn = self.engine.raw_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(schema_sql)
            conn.commit()
        except Exception as exc:
            self.logger.error(
                "Failed to ensure PostgreSQL schema; continuing with CSV export only (%s)",
                exc.__class__.__name__,
            )
            self.available = False
        finally:
            conn.close()

    def upsert_business(self, business: Business) -> Optional[str]:
        if not self.available:
            return None
        try:
            if self.backend == "supabase":
                return self._supabase_upsert_business(business)
            if self.backend == "postgres":
                return self._postgres_upsert_business(business)
        except Exception as exc:
            self._handle_db_error("business upsert", exc)
        return None

    def upsert_audit(self, business_id: Optional[str], audit: WebsiteAudit) -> Optional[str]:
        if not self.available or not business_id:
            return None
        try:
            if self.backend == "supabase":
                return self._supabase_insert_audit(business_id, audit)
            if self.backend == "postgres":
                return self._postgres_upsert_audit(business_id, audit)
        except Exception as exc:
            self._handle_db_error("audit insert", exc)
        return None

    def upsert_outreach(self, audit_id: Optional[str], draft: OutreachDraft) -> Optional[str]:
        if not self.available or not audit_id:
            return None
        try:
            if self.backend == "supabase":
                return self._supabase_insert_outreach(audit_id, draft)
            if self.backend == "postgres":
                return self._postgres_upsert_outreach(audit_id, draft)
        except Exception as exc:
            self._handle_db_error("outreach insert", exc)
        return None

    def update_outreach_delivery(
        self,
        outreach_id: Optional[str],
        recipient_email: str | None,
        send_status: str,
        send_error: str | None = None,
    ) -> None:
        if not self.available or not outreach_id:
            return
        try:
            if self.backend == "supabase":
                self._supabase_update_outreach_delivery(outreach_id, recipient_email, send_status, send_error)
                return
            if self.backend == "postgres":
                self._postgres_update_outreach_delivery(outreach_id, recipient_email, send_status, send_error)
                return
        except Exception as exc:
            self._handle_db_error("outreach delivery update", exc)

    def get_next_queue_item(self) -> Optional[NicheCityQueueItem]:
        if not self.available:
            return None
        try:
            if self.backend == "supabase":
                return self._supabase_get_next_queue_item()
            if self.backend == "postgres":
                return self._postgres_get_next_queue_item()
        except Exception as exc:
            self._handle_db_error("queue fetch", exc)
        return None

    def mark_queue_item_started(self, queue_item_id: int) -> None:
        if not self.available:
            return
        try:
            if self.backend == "supabase":
                self._supabase_mark_queue_item_started(queue_item_id)
                return
            if self.backend == "postgres":
                self._postgres_mark_queue_item_started(queue_item_id)
                return
        except Exception as exc:
            self._handle_db_error("queue update", exc)

    def mark_queue_item_exhausted(self, queue_item_id: int) -> None:
        if not self.available:
            return
        try:
            if self.backend == "supabase":
                self._supabase_mark_queue_item_exhausted(queue_item_id)
                return
            if self.backend == "postgres":
                self._postgres_mark_queue_item_exhausted(queue_item_id)
                return
        except Exception as exc:
            self._handle_db_error("queue exhaustion update", exc)

    def get_seen_domains_for_queue_item(self, queue_item_id: int) -> set[str]:
        if not self.available:
            return set()
        try:
            if self.backend == "supabase":
                return self._supabase_get_seen_domains_for_queue_item(queue_item_id)
            if self.backend == "postgres":
                return self._postgres_get_seen_domains_for_queue_item(queue_item_id)
        except Exception as exc:
            self._handle_db_error("seen domain fetch", exc)
        return set()

    def add_seen_domain_for_queue_item(self, queue_item_id: int, normalized_domain: str) -> None:
        if not self.available or not normalized_domain:
            return
        try:
            if self.backend == "supabase":
                self._supabase_add_seen_domain_for_queue_item(queue_item_id, normalized_domain)
                return
            if self.backend == "postgres":
                self._postgres_add_seen_domain_for_queue_item(queue_item_id, normalized_domain)
                return
        except Exception as exc:
            self._handle_db_error("seen domain insert", exc)

    def _handle_db_error(self, operation: str, exc: Exception) -> None:
        self.logger.error(
            "Database %s failed; continuing with CSV export only (%s)",
            operation,
            exc.__class__.__name__,
        )
        if self.backend == "supabase":
            self.supabase = None
        if self.backend == "postgres":
            self.engine = None
        self.available = False
        self.backend = "disabled"

    def _supabase_public(self):
        if self.supabase is None:
            return None
        schema_method = getattr(self.supabase, "schema", None)
        if callable(schema_method):
            return schema_method("public")
        return self.supabase

    def _supabase_upsert_business(self, business: Business) -> Optional[str]:
        client = self._supabase_public()
        if client is None:
            return None

        payload = business.model_dump()
        response = (
            client.table("businesses")
            .upsert(payload, on_conflict="normalized_domain")
            .execute()
        )
        data = response.data or []
        if not data:
            lookup = (
                client.table("businesses")
                .select("id")
                .eq("normalized_domain", business.normalized_domain)
                .limit(1)
                .execute()
            )
            data = lookup.data or []
        return str(data[0]["id"]) if data else None

    def _supabase_insert_audit(self, business_id: str, audit: WebsiteAudit) -> Optional[str]:
        client = self._supabase_public()
        if client is None:
            return None

        payload = audit.model_dump()
        payload["business_id"] = business_id
        response = client.table("website_audits").insert(payload).execute()
        data = response.data or []
        return str(data[0]["id"]) if data else None

    def _supabase_insert_outreach(self, audit_id: str, draft: OutreachDraft) -> Optional[str]:
        client = self._supabase_public()
        if client is None:
            return None

        payload = {"audit_id": audit_id, **draft.model_dump()}
        response = client.table("outreach_drafts").insert(payload).execute()
        data = response.data or []
        return str(data[0]["id"]) if data else None

    def _supabase_update_outreach_delivery(
        self,
        outreach_id: str,
        recipient_email: str | None,
        send_status: str,
        send_error: str | None,
    ) -> None:
        client = self._supabase_public()
        if client is None:
            return

        payload: dict[str, object] = {
            "recipient_email": recipient_email,
            "send_status": send_status,
            "send_error": send_error,
        }
        if send_status == "sent":
            payload["sent_at"] = datetime.now(timezone.utc).isoformat()
        client.table("outreach_drafts").update(payload).eq("id", outreach_id).execute()

    def _supabase_get_next_queue_item(self) -> Optional[NicheCityQueueItem]:
        client = self._supabase_public()
        if client is None:
            return None

        response = (
            client.table("niche_city_queue")
            .select("*")
            .eq("active", True)
            .eq("is_exhausted", False)
            .order("priority", desc=True)
            .order("last_run_at", desc=False, nullsfirst=True)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        data = response.data or []
        return NicheCityQueueItem.model_validate(data[0]) if data else None

    def _supabase_mark_queue_item_started(self, queue_item_id: int) -> None:
        client = self._supabase_public()
        if client is None:
            return

        current = (
            client.table("niche_city_queue")
            .select("runs_count")
            .eq("id", queue_item_id)
            .limit(1)
            .execute()
        )
        runs_count = 0
        if current.data:
            runs_count = int(current.data[0].get("runs_count") or 0)

        client.table("niche_city_queue").update(
            {
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "runs_count": runs_count + 1,
            }
        ).eq("id", queue_item_id).execute()

    def _supabase_mark_queue_item_exhausted(self, queue_item_id: int) -> None:
        client = self._supabase_public()
        if client is None:
            return

        client.table("niche_city_queue").update(
            {
                "is_exhausted": True,
                "active": False,
            }
        ).eq("id", queue_item_id).execute()

    def _supabase_get_seen_domains_for_queue_item(self, queue_item_id: int) -> set[str]:
        client = self._supabase_public()
        if client is None:
            return set()

        response = (
            client.table("niche_city_seen_domains")
            .select("normalized_domain")
            .eq("queue_item_id", queue_item_id)
            .execute()
        )
        return {
            str(row["normalized_domain"]).lower()
            for row in (response.data or [])
            if row.get("normalized_domain")
        }

    def _supabase_add_seen_domain_for_queue_item(self, queue_item_id: int, normalized_domain: str) -> None:
        client = self._supabase_public()
        if client is None:
            return

        payload = {
            "queue_item_id": queue_item_id,
            "normalized_domain": normalized_domain.lower(),
        }
        client.table("niche_city_seen_domains").upsert(
            payload,
            on_conflict="queue_item_id,normalized_domain",
        ).execute()

    def _postgres_upsert_business(self, business: Business) -> Optional[str]:
        if self.engine is None:
            return None

        select_by_domain = text(
            """
            SELECT id
            FROM businesses
            WHERE normalized_domain = :normalized_domain
            LIMIT 1
            """
        )
        select_by_name_location = text(
            """
            SELECT id
            FROM businesses
            WHERE normalized_name = :normalized_name
              AND location = :location
            LIMIT 1
            """
        )
        update_existing = text(
            """
            UPDATE businesses
            SET
                name = :name,
                normalized_name = :normalized_name,
                location = :location,
                website_url = :website_url,
                normalized_domain = :normalized_domain,
                phone = COALESCE(:phone, phone),
                email = COALESCE(:email, email),
                source_url = :source_url,
                updated_at = NOW()
            WHERE id = :id
            RETURNING id
            """
        )
        insert_new = text(
            """
            INSERT INTO businesses (
                name, normalized_name, location, website_url, normalized_domain, phone, email, source_url
            ) VALUES (
                :name, :normalized_name, :location, :website_url, :normalized_domain, :phone, :email, :source_url
            )
            RETURNING id
            """
        )
        payload = business.model_dump()
        with self.engine.begin() as conn:
            existing_id = conn.execute(select_by_domain, payload).scalar_one_or_none()
            if existing_id is None:
                existing_id = conn.execute(select_by_name_location, payload).scalar_one_or_none()
            if existing_id is not None:
                payload["id"] = existing_id
                return str(conn.execute(update_existing, payload).scalar_one())
            return str(conn.execute(insert_new, payload).scalar_one())

    def _postgres_upsert_audit(self, business_id: str, audit: WebsiteAudit) -> Optional[str]:
        if self.engine is None:
            return None

        payload = audit.model_dump()
        payload["business_id"] = business_id
        payload["outdated_design_signals"] = json.dumps(payload["outdated_design_signals"])
        payload["notes"] = json.dumps(payload["notes"])
        payload["issue_summary"] = json.dumps(payload["issue_summary"])

        query = text(
            """
            INSERT INTO website_audits (
                business_id, checked_at, audit_status, blocked_reason, final_url, http_status, https_enabled, ssl_valid,
                load_time_seconds, mobile_viewport, missing_title, missing_meta_description,
                broken_images_count, broken_internal_links_count, has_contact_form, has_mailto,
                has_phone_link, outdated_design_signals, unreachable, blocked_or_challenged,
                notes, score, issue_summary
            ) VALUES (
                :business_id, :checked_at, :audit_status, :blocked_reason, :final_url, :http_status, :https_enabled, :ssl_valid,
                :load_time_seconds, :mobile_viewport, :missing_title, :missing_meta_description,
                :broken_images_count, :broken_internal_links_count, :has_contact_form, :has_mailto,
                :has_phone_link, CAST(:outdated_design_signals AS jsonb), :unreachable, :blocked_or_challenged,
                CAST(:notes AS jsonb), :score, CAST(:issue_summary AS jsonb)
            )
            RETURNING id
            """
        )
        with self.engine.begin() as conn:
            return str(conn.execute(query, payload).scalar_one())

    def _postgres_upsert_outreach(self, audit_id: str, draft: OutreachDraft) -> Optional[str]:
        if self.engine is None:
            return None

        payload = {"audit_id": audit_id, **draft.model_dump()}
        query = text(
            """
            INSERT INTO outreach_drafts (
                audit_id, subject, body, recipient_email, send_status, sent_at, send_error
            )
            VALUES (
                :audit_id, :subject, :body, :recipient_email, :send_status, :sent_at, :send_error
            )
            RETURNING id
            """
        )
        with self.engine.begin() as conn:
            return str(conn.execute(query, payload).scalar_one())

    def _postgres_update_outreach_delivery(
        self,
        outreach_id: str,
        recipient_email: str | None,
        send_status: str,
        send_error: str | None,
    ) -> None:
        if self.engine is None:
            return

        query = text(
            """
            UPDATE outreach_drafts
            SET
                recipient_email = :recipient_email,
                send_status = :send_status,
                send_error = :send_error,
                sent_at = CASE WHEN :send_status = 'sent' THEN NOW() ELSE sent_at END
            WHERE id = :outreach_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                query,
                {
                    "outreach_id": outreach_id,
                    "recipient_email": recipient_email,
                    "send_status": send_status,
                    "send_error": send_error,
                },
            )

    def _postgres_get_next_queue_item(self) -> Optional[NicheCityQueueItem]:
        if self.engine is None:
            return None

        query = text(
            """
            SELECT *
            FROM niche_city_queue
            WHERE active = TRUE
              AND is_exhausted = FALSE
            ORDER BY priority DESC, last_run_at NULLS FIRST, created_at ASC
            LIMIT 1
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(query).mappings().first()
            return NicheCityQueueItem.model_validate(dict(row)) if row else None

    def _postgres_mark_queue_item_started(self, queue_item_id: int) -> None:
        if self.engine is None:
            return

        query = text(
            """
            UPDATE niche_city_queue
            SET
                last_run_at = NOW(),
                runs_count = runs_count + 1
            WHERE id = :queue_item_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(query, {"queue_item_id": queue_item_id})

    def _postgres_mark_queue_item_exhausted(self, queue_item_id: int) -> None:
        if self.engine is None:
            return

        query = text(
            """
            UPDATE niche_city_queue
            SET
                is_exhausted = TRUE,
                active = FALSE
            WHERE id = :queue_item_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(query, {"queue_item_id": queue_item_id})

    def _postgres_get_seen_domains_for_queue_item(self, queue_item_id: int) -> set[str]:
        if self.engine is None:
            return set()

        query = text(
            """
            SELECT normalized_domain
            FROM niche_city_seen_domains
            WHERE queue_item_id = :queue_item_id
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"queue_item_id": queue_item_id}).scalars().all()
            return {str(value).lower() for value in rows if value}

    def _postgres_add_seen_domain_for_queue_item(self, queue_item_id: int, normalized_domain: str) -> None:
        if self.engine is None:
            return

        query = text(
            """
            INSERT INTO niche_city_seen_domains (queue_item_id, normalized_domain)
            VALUES (:queue_item_id, :normalized_domain)
            ON CONFLICT (queue_item_id, normalized_domain) DO NOTHING
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                query,
                {
                    "queue_item_id": queue_item_id,
                    "normalized_domain": normalized_domain.lower(),
                },
            )
