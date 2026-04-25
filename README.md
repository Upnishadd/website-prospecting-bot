# Website Issue Prospecting Bot

Website Issue Prospecting Bot is a local-first Python system for finding business websites, auditing them for visible issues, storing the results in PostgreSQL or Supabase, generating compliant outreach drafts, and exporting the final dataset to CSV.

## Features

- Search for local business websites by niche and location
- Collect public business details:
  - business name
  - website URL
  - phone number
  - public email
  - source URL
- Audit websites for:
  - HTTP status and final URL
  - HTTPS availability
  - SSL certificate validity
  - page load time
  - mobile viewport support
  - missing title and meta description
  - broken images
  - broken internal links
  - contact form and contact link presence
  - outdated design signals
  - blocked or challenged responses
  - unreachable or down sites
- Score websites from 0 to 100
- Store businesses, audits, and outreach drafts with deduplication
- Export run results to CSV
- Use retries, backoff, rate limiting, robots.txt checks, clear user-agent headers, and random delays

## Project Structure

```text
website-prospecting-bot/
  README.md
  requirements.txt
  .env.example
  schema.sql
  main.py
  config.py
  models.py
  db.py
  scraper.py
  auditor.py
  scorer.py
  outreach.py
  exporter.py
  email_sender.py
  utils.py
  tests/
    test_scorer.py
    test_auditor.py
```

## Setup

1. Create and activate a Python 3.11 virtual environment.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in the values.

## Environment Variables

See `.env.example` for the full list. The main values are:

- `SUPABASE_URL`: your Supabase project URL
- `SUPABASE_SECRET_KEY`: server-side Supabase secret key for local runs or backend jobs only
- `DATABASE_URL`: optional PostgreSQL fallback if you are not using the Supabase API client
- `ENABLE_EMAIL_SENDING`: set to `true` to actually send outreach emails
- `GMAIL_SENDER_EMAIL`: Gmail address used to send outreach
- `GMAIL_APP_PASSWORD`: Gmail app password for SMTP
- `EMAIL_SENDER_NAME`: display name used in the `From` header
- `USER_AGENT`: clear identification string for polite crawling
- `REQUEST_TIMEOUT`
- `MAX_REDIRECTS`
- `MIN_DELAY_SECONDS`
- `MAX_DELAY_SECONDS`
- `MAX_REQUESTS_PER_DOMAIN`
- `MAX_ASSET_CHECKS`
- `SEARCH_PROVIDER`
- `SEARCH_BASE_URL`

## Database Setup

This project ships with [schema.sql](/c:/Users/upnis/Documents/WebsiteCleanerBot/schema.sql).

To create the required tables in Supabase/PostgreSQL:

1. Open the SQL editor in Supabase, or connect to your PostgreSQL database.
2. Run the contents of `schema.sql`.

Supabase integration notes:

- The app prefers `SUPABASE_URL` plus `SUPABASE_SECRET_KEY` when they are set.
- Business records are upserted by `normalized_domain`.
- Website audits are inserted as new rows.
- Outreach drafts are inserted as new rows.
- Outreach drafts can optionally be sent via Gmail SMTP when email sending is enabled.
- If Supabase is unavailable, the app logs a sanitized error and still completes CSV export.
- Service role keys are never written to logs by the app.

Tables created:

- `businesses`
- `website_audits`
- `outreach_drafts`
- `niche_city_queue`
- `niche_city_seen_domains`

Important audit fields:

- `audit_status`: `success`, `unreachable`, `blocked_or_challenged`, `login_required`, `paywalled`, `timeout`, or `error`
- `blocked_reason`: short machine-readable reason such as CAPTCHA, WAF challenge, login wall, paywall, robots restriction, timeout, or redirect limit

Deduplication strategy:

- `businesses.normalized_domain` is unique
- `(normalized_name, location)` is unique
- `website_audits` stores a new row for each audit run
- `outreach_drafts` stores a new row for each generated draft

## Running Locally

Example:

```powershell
python main.py --niche "plumbers" --location "Melbourne" --max-results 50
```

Queue-driven example:

```powershell
python main.py
```

What the run does:

- finds candidate business websites
- audits each site politely
- stores businesses and audits in the database
- generates outreach drafts for lower-scoring sites
- exports the run to `exports/`
- prints a summary
- if `--niche` and `--location` are omitted, it pulls the next active non-exhausted row from `niche_city_queue`
- if a queue-driven run completes with no successful business insert, that queue row is marked exhausted
- queue-driven runs remember previously seen domains per niche/city row and skip them on later runs so the bot does not keep cycling the same first websites

To skip CSV export for a specific run:

```powershell
python main.py --niche "plumbers" --location "Melbourne" --max-results 50 --no-csv
```

## Ethical and Compliance Notes

This bot is intentionally conservative.

- It does not bypass CAPTCHA, login walls, paywalls, or anti-bot protections.
- It does not use browser automation to work around bot checks or challenge pages.
- It checks `robots.txt` where practical before fetching pages.
- It uses configurable delays, rate limits, and exponential backoff.
- It detects and classifies CAPTCHA pages, Cloudflare/WAF blocks, login walls, paywalls, rate limits, and challenge pages, then skips them safely.
- It only collects publicly visible business contact details.
- Email sending is disabled by default and only happens when explicitly enabled.
- It does not expose Supabase keys in logs.

If a site blocks automated access, the system skips it instead of escalating.

## Testing

Run:

```powershell
pytest
```

The test suite covers:

- score calculation
- URL normalization
- audit parsing
- blocked-page detection

## Deployment Suggestions

Later, when you deploy this:

- run it as a scheduled job on a VPS or container platform
- use Supabase-managed Postgres for storage
- write exports to object storage if needed
- consider a job queue for large prospecting batches
- add structured logging and monitoring
- keep concurrency low and delays conservative

## GitHub Actions Automation

This repo includes a scheduled workflow at `.github/workflows/prospecting-bot.yml`.

What it does:

- runs on a daily schedule
- can also be started manually with `workflow_dispatch`
- uses repository secrets for Supabase
- can send outreach emails through Gmail SMTP when enabled
- disables CSV export during Actions runs
- updates `docs/automation-heartbeat.md` after every run to keep the public repository active
- defaults to queue-driven mode unless you manually provide niche and location overrides

Recommended repository secrets:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `GMAIL_SENDER_EMAIL`
- `GMAIL_APP_PASSWORD`

Recommended repository variables:

- `PROSPECT_MAX_RESULTS`
- `ENABLE_EMAIL_SENDING`
- `EMAIL_SENDER_NAME`

Important safety notes for a public repo:

- Do not commit a real `.env` file.
- Keep Supabase credentials only in GitHub Actions repository secrets.
- Keep Gmail credentials only in GitHub Actions repository secrets.
- The workflow does not print secrets or write them into the heartbeat file.
- The heartbeat file contains only safe run metadata like time, niche, location, and status.
- Scheduled GitHub Actions workflows run on the default branch and can be delayed during busy periods.

## Notes on Search Sources

The default implementation uses a configurable web search endpoint and conservative HTML parsing. Search result layouts can change over time, so `SEARCH_PROVIDER` and `SEARCH_BASE_URL` are configurable in `.env`.

For more stable long-term use, you may later swap the discovery step to a compliant business directory API or a licensed search API without changing the rest of the pipeline.
