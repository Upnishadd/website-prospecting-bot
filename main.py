from __future__ import annotations

import argparse

from auditor import WebsiteAuditor
from config import BASE_DIR, load_settings
from db import Database
from email_sender import send_outreach_email
from exporter import export_to_csv
from models import ExportRow, SummaryStats
from outreach import generate_outreach_draft
from scorer import score_audit
from scraper import BusinessScraper
from utils import PoliteHttpClient, RobotsCache, get_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Website Issue Prospecting Bot")
    parser.add_argument("--niche", help="Business niche to search for")
    parser.add_argument("--location", help="Location to prospect")
    parser.add_argument("--max-results", type=int, help="Maximum businesses to inspect")
    parser.add_argument("--no-csv", action="store_true", help="Skip CSV export for this run")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = load_settings()
    logger = get_logger(settings.log_level)

    db = Database(settings, BASE_DIR / "schema.sql", logger)
    db.ensure_schema()
    db.check_connection_and_schema()

    queue_item = None
    niche = args.niche
    location = args.location
    max_results = args.max_results
    excluded_domains: set[str] = set()

    if (niche and not location) or (location and not niche):
        raise SystemExit("Pass both --niche and --location together, or omit both to use niche_city_queue")

    if not niche or not location:
        queue_item = db.get_next_queue_item()
        if queue_item is None:
            raise SystemExit("No active, non-exhausted niche/city pair found in niche_city_queue")
        niche = queue_item.niche_name
        location = queue_item.city
        max_results = max_results or queue_item.audit_limit
        excluded_domains = db.get_seen_domains_for_queue_item(queue_item.id)
        db.mark_queue_item_started(queue_item.id)

    if max_results is None:
        max_results = 50

    logger.info("Starting prospecting run for niche=%s location=%s", niche, location)

    robots_cache = RobotsCache(settings, logger)
    client = PoliteHttpClient(settings, logger, robots_cache=robots_cache)

    try:
        scraper = BusinessScraper(settings, client, logger)
        auditor = WebsiteAuditor(settings, client, logger)

        businesses = scraper.discover_businesses_with_seen_domains(
            niche=niche,
            location=location,
            max_results=max_results,
            excluded_domains=excluded_domains,
        )
        logger.info("Discovered %s business candidates", len(businesses))

        export_rows: list[ExportRow] = []
        summary = SummaryStats()
        successful_business_inserts = 0

        for business in businesses:
            summary.total_checked += 1
            try:
                if queue_item is not None:
                    db.add_seen_domain_for_queue_item(queue_item.id, business.normalized_domain)
                business_id = db.upsert_business(business)
                if business_id:
                    successful_business_inserts += 1
                audit = auditor.audit(business)
                audit.score, audit.issue_summary = score_audit(audit)
                audit_id = db.upsert_audit(business_id, audit)

                draft_subject = None
                draft_body = None
                outreach_allowed = audit.audit_status == "success" and audit.score <= settings.max_outreach_score
                if outreach_allowed:
                    draft = generate_outreach_draft(business, audit)
                    draft.recipient_email = business.email
                    outreach_id = db.upsert_outreach(audit_id, draft)
                    send_result = send_outreach_email(settings, business, draft)
                    db.update_outreach_delivery(
                        outreach_id=outreach_id,
                        recipient_email=business.email,
                        send_status=send_result.status,
                        send_error=send_result.error,
                    )
                    draft_subject = draft.subject
                    draft_body = draft.body

                export_rows.append(
                    ExportRow(
                        business_name=business.name,
                        location=business.location,
                        website_url=business.website_url,
                        phone=business.phone,
                        email=business.email,
                        source_url=business.source_url,
                        audit_status=audit.audit_status,
                        blocked_reason=audit.blocked_reason,
                        final_url=audit.final_url,
                        http_status=audit.http_status,
                        https_enabled=audit.https_enabled,
                        ssl_valid=audit.ssl_valid,
                        load_time_seconds=audit.load_time_seconds,
                        mobile_viewport=audit.mobile_viewport,
                        missing_title=audit.missing_title,
                        missing_meta_description=audit.missing_meta_description,
                        broken_images_count=audit.broken_images_count,
                        broken_internal_links_count=audit.broken_internal_links_count,
                        has_contact_form=audit.has_contact_form,
                        has_mailto=audit.has_mailto,
                        has_phone_link=audit.has_phone_link,
                        outdated_design_signals=audit.outdated_design_signals,
                        unreachable=audit.unreachable,
                        blocked_or_challenged=audit.blocked_or_challenged,
                        score=audit.score,
                        issue_summary=audit.issue_summary,
                        outreach_subject=draft_subject,
                        outreach_body=draft_body,
                    )
                )

                if outreach_allowed:
                    summary.bad_websites_found += 1
                if audit.unreachable or audit.audit_status == "unreachable":
                    summary.unreachable_sites += 1
                if audit.audit_status == "success" and not audit.https_enabled:
                    summary.missing_https += 1
                if audit.audit_status == "success" and audit.load_time_seconds is not None and audit.load_time_seconds > 4:
                    summary.slow_sites += 1
            except Exception as exc:
                logger.exception("Failed processing business %s (%s): %s", business.name, business.website_url, exc)
                continue

        if settings.enable_csv_export and not args.no_csv:
            csv_path = export_to_csv(export_rows, settings.export_dir, niche, location)
            summary.csv_path = str(csv_path)
        else:
            summary.csv_path = "disabled"

        if queue_item is not None and successful_business_inserts == 0:
            db.mark_queue_item_exhausted(queue_item.id)
            logger.info(
                "Marked niche/city pair as exhausted because no business insert succeeded for queue item id=%s",
                queue_item.id,
            )

        print(f"total checked: {summary.total_checked}")
        print(f"bad websites found: {summary.bad_websites_found}")
        print(f"unreachable sites: {summary.unreachable_sites}")
        print(f"missing HTTPS: {summary.missing_https}")
        print(f"slow sites: {summary.slow_sites}")
        print(f"CSV path: {summary.csv_path}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
