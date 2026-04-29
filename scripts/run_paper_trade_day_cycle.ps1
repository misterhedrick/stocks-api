$ErrorActionPreference = "Stop"

$env:MARKET_CYCLE_SCAN_ENABLED = "true"
$env:MARKET_CYCLE_RECONCILE_ENABLED = "true"
$env:MARKET_CYCLE_PREVIEW_ENABLED = "true"
$env:MARKET_CYCLE_EXIT_ENABLED = "true"
$env:MARKET_CYCLE_NEWS_ENABLED = "false"
$env:MARKET_CYCLE_SUBMIT_ENABLED = "true"
$env:TRADING_AUTOMATION_ENABLED = "true"
$env:AUTO_SUBMIT_REQUIRES_PAPER = "true"
$env:MAX_AUTO_ORDERS_PER_CYCLE = "50"
$env:MAX_AUTO_ORDERS_PER_DAY = "50"
$env:MAX_OPEN_POSITIONS = "100"
$env:MAX_OPEN_POSITIONS_PER_SYMBOL = "100"
$env:MAX_CONTRACTS_PER_ORDER = "1"
$env:MAX_ESTIMATED_PREMIUM_PER_ORDER = "250"

.\.venv\Scripts\python.exe .\scripts\run_market_cycle_smoke.py `
  --scan-limit 100 `
  --order-limit 100 `
  --fill-page-size 100
