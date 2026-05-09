# Per-symbol entry limit change

Completed update for `render.yaml` on `develop`:

Each symbol-specific `market-entry-cycle` cron was changed from:

```text
scan_limit=25&order_limit=25&fill_page_size=50
```

to:

```text
scan_limit=100&order_limit=100&fill_page_size=100
```

Symbols:

- SPY
- QQQ
- AAPL
- MSFT
- NVDA

The old combined `stocks-api-market-cycle` cron has been removed. Scheduled entries now come from the five symbol-specific `market-entry-cycle` cron jobs; exits and maintenance remain separate global jobs.
