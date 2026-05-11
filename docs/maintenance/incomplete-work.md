# Incomplete work / pending project notes

This file captures work that is known to be incomplete or intentionally deferred so it is not lost between sessions.

## Incomplete AI review layer

Not implemented yet:

- External LLM-backed review generation.
- Automatic application of approved suggestions to strategy config.

Important rule: AI may recommend strategy changes only. It must not directly modify live strategy logic or deployed trading behavior.

## Paper testing and tuning still pending

- Paper-test the full evaluator-backed strategy set.
- Compare signal volume by scanner type.
- Compare no-signal reasons by scanner type.
- Review `option_selection_diagnostics` for rejected contract candidates.
- Tune scanner thresholds by strategy type.
- Tune `PAPER_PREVIEW_PROFILE_<PROFILE>_*` settings by strategy type.

## Option contract selection improvements still pending

Current option selection is first-pass and still needs better scoring/filters as broker data allows:

- Delta / Greeks-aware selection.
- Moneyness-aware selection.
- Better liquidity scoring.
- Quote-quality scoring.
- More structured comparison between rejected candidates and closed trade outcomes.

## Testing / local infrastructure still pending

Not implemented yet:

- Formal state enums/state machine for currently string-based statuses.

## Operational limitations still present

- News scanning is lightweight RSS/headline gating only.
- Render cron schedules are UTC-only and must be reviewed around DST changes.
- Symbol-specific entry crons may increase monthly Render cost because each cron service can count separately.
