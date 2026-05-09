# Per-symbol entry limit change

Needed update for `render.yaml` on `develop`:

Change each symbol-specific `market-entry-cycle` cron from:

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

Leave the old combined `market-cycle` fallback unchanged unless intentionally re-enabling it at full size.
