"""jobtrends — the hiring-market data engine behind Bullshit or Fit.

This round ships ingestion only: it pulls Hacker News "Who is hiring?" monthly
threads via the HN Algolia API and stores RAW post text in Postgres (its own
`jobtrends` schema inside the Bullshit or Fit database). Analysis, extraction,
and any product surface come later and reconstruct everything from the raw rows.
"""
