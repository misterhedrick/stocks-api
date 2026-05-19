# Strategy Tuning Research Prompt

Use this prompt when you want the AI to research the current paper-trading evidence and propose a practical batch of tuning changes.

The goal is not to make one tiny change at the research stage. The goal is to quickly identify the best tuning opportunities, group them by problem type, and propose a small batch of human-reviewed changes that can be applied intentionally.

## Prompt

```text
Work from develop.

Do a strategy tuning research pass for the paper-trading system. Use the latest saved evidence from:

- /api/v1/automation/strategy-refinement
- /api/v1/automation/learning-report
- /api/v1/automation/paper-review-snapshots
- /api/v1/automation/performance
- /api/v1/automation/strategy-change-suggestions
- /api/v1/automation/strategy-tuning-decisions
- trade_cases
- option_selection_diagnostics
- signals
- order_intents
- job_runs

Also read:

- docs/maintenance/strategy-refinement-playbook.md
- docs/signal-strategies/
- ai_refresh.txt

Do not apply changes yet.
Do not change live/master.
Do not let AI automatically rewrite strategy logic.
Keep all recommendations paper-only and human-review-only.

Research goals:

1. Confirm the data pipeline is healthy:
   - latest post-market maintenance ran
   - latest paper_review_snapshot exists
   - learning_report was saved
   - strategy-refinement summary has candidates
   - no major job failures or reconciliation gaps are distorting the evidence

2. Identify top tuning candidates:
   - include candidates with minimum evidence met
   - include near-ready candidates if they reveal obvious mechanical problems
   - group candidates by scanner_type, symbol, and problem type

3. Classify each issue as one of:
   - runtime/data issue
   - option-selection issue
   - signal-threshold issue
   - exit/risk issue
   - schedule/universe issue

4. Propose a batch of tuning changes:
   - prefer 3 to 7 total proposed changes
   - each change should touch one scanner/symbol/profile area
   - each change should modify only one or two knobs
   - separate config/env tuning from code changes
   - do not bundle unrelated changes together
   - prefer scanner/profile tuning before removing a symbol from the paper universe unless the user explicitly approves a symbol pause

5. For each proposed change, include:
   - scanner_type
   - symbol or preview profile
   - problem classification
   - evidence summary
   - exact config/env keys involved
   - proposed before and after values
   - expected effect
   - risk
   - rollback criteria
   - how many post-market snapshots to wait before judging
   - whether it should be recorded as a strategy_tuning_decision

6. Prioritize the batch:
   - P0: fix data/runtime/schedule issues before tuning strategy behavior
   - P1: option-selection blockers that prevent trades from becoming measurable
   - P2: exit/risk changes where entries look usable but outcomes are poor
   - P3: signal-threshold changes where no-signal or bad-signal evidence is strong
   - P4: exploratory changes for low-evidence but promising strategies

7. Produce a final research report with:
   - health check summary
   - top candidates table
   - proposed tuning batch
   - changes explicitly not recommended yet
   - decision records that should be created
   - tests or smoke checks to run after implementation

Stop after the research report. Do not apply changes unless I explicitly approve the batch or specific items.
```

## Follow-Up Approval Prompt

Use this after reviewing the research report.

```text
Approved: apply tuning items [list item numbers].

Work from develop only.
Record strategy_tuning_decision entries before applying the changes.
Apply only the approved items.
Keep changes paper-only.
Run relevant tests.
Summarize exactly what changed, what evidence drove it, and how we will judge the outcome after future snapshots.
```

## Follow-Up Results Prompt

Use this after several post-market snapshots have accumulated.

```text
Review the before/after results for recent strategy_tuning_decisions.

Use /api/v1/automation/strategy-refinement and saved paper_review_snapshots.
For each applied decision, compare:

- priority score before vs after
- closed trade outcomes
- preview rejection rate
- option diagnostic reasons
- no-signal reasons
- job/runtime health

Update the decision outcome summaries with improved, worsened, inconclusive, or needs more data.
Do not propose new changes until the existing decisions are judged.
```

## Guardrails

- Broad research is allowed.
- Batch proposals are allowed.
- Automatic application is not allowed.
- Strategy logic changes require explicit human approval.
- Prefer env/config tuning before code changes unless the evidence shows a code defect.
- Do not recommend pausing SPY by default. Treat SPY losses as evidence for strategy-type tuning unless a later human decision explicitly approves a SPY pause.
- If the data pipeline is unhealthy, fix that first.
- Do not optimize for trade count alone. Optimize for measurable, explainable paper outcomes.

