from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    spark_swarm_api_url: str = "https://sparkswarm.com/api/v1"
    spark_slug: str = "bullshit-or-fit"
    environment: str = "dev"
    cors_origins: str = "https://bullshitorfit.com,https://www.bullshitorfit.com"

    # --- Database (used by the jobtrends data engine only; the landing/lead web app
    # never touches Postgres, so an unset/broken URL cannot keep the site from booting). ---
    database_url: str = "postgresql+psycopg://bullshit_or_fit:bullshit_or_fit@localhost:5432/bullshit_or_fit_db"

    # --- jobtrends ingest knobs (HN "Who is hiring?" corpus) ---
    # HN Algolia is a public API — no key. Bucket = the thread's month.
    jobtrends_hn_base_url: str = "https://hn.algolia.com/api/v1"
    jobtrends_user_agent: str = "jobtrends/0.1 (+https://bullshitorfit.com)"
    # First boot pulls this many months; each subsequent tick re-ingests only the
    # trailing window (idempotent upsert, so re-runs are cheap and never duplicate).
    jobtrends_backfill_months: int = 18
    jobtrends_recent_months: int = 2
    # Daily loop: a month's thread is caught within a day of being posted.
    jobtrends_ingest_interval_seconds: int = 86_400

    # --- USAJobs (federal jobs) — free self-service API key from
    # developer.usajobs.gov, emailed on signup. Empty key = snapshot skips. The
    # User-Agent must be a contact email per USAJobs' terms. ---
    usajobs_api_key: str = ""
    usajobs_user_agent: str = ""
    usajobs_max_pages: int = 4  # x500 results/page = bounded recent sample


settings = Settings()
