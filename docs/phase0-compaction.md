# Phase 0 — bounded disposable checkpoint caches

Measured on the shallow-clone checkpoint, 2026-11-25: **28,237,357 → 6,304,346 bytes** (target <=8 MB).
`posted`, `publication_history`, `processed`, `last_delivery` compared exactly equal before/after.
Rehearsal: `DRY_RUN=1 python tools/compact_checkpoint.py --input data/checkpoint.json --output-dir /tmp/separate-empty-dir`.
The production `data/` in this PR is unchanged; migration runs on load after code deployment.

- assessment_cache: TTL max(48 h, 2*MAX_DEAL_AGE_HOURS), newest 3,000 records.
- price_events: TTL 14 days, newest 3,000 records. Live dict references preserved.
- Load compaction is idempotent; the first migration writes a durable compressed original in data/migrations/ before replacing canonical state.
- Existing plain JSON canonical/exports/previous-generation backup remain for backward-compatible reads and operational review.
- First backup may retain the large original until the next save rotates it; the compressed migration original remains.
- Remote semantic merge applies the same retention so stale remote caches cannot resurrect discarded entries.
- Artifact per-file cap is 64 MiB to accept the actual legacy generation during migration, still bounded; schema validation and symlink rejection remain.
- New maintenance metadata is validated and merged by newest compaction timestamp.
- No discount/price parsing policy changed; RULES_VERSION/PARSER_VERSION unchanged.

## Proposed only: stop history growth
1. Dedicated state branch isolates history from code clones; requires explicit checkout/recovery changes and rollout.
2. Fewer state commits reduce object churn, but increase unsaved delivery window; require a separate durable outbox strategy.
3. Compressed canonical storage and less redundant exports reduce working-tree size, but need compatible read/recovery tooling.
None of these changes is executed by this phase. No Telegram request is made by migration/tests.
