# V1 Freeze Audit

- Base commit: `2f18e262bfdef916e1dd4cc28f61aff7d4b5a4c2`
- Branch: `master`
- Freeze method: hashed manifest (a clean commit is unsafe because the worktree contains mixed existing research, campaign, environment, and V1 changes).
- V1 runtime changes: backend configuration, API, schemas, MT5 adapter, prediction/risk/journal/metrics services, strategy and analysis sources, and terminal UI.
- Preserved unrelated changes: research/certification reports, campaign directories, certified datasets, import/validation scripts, alternate environments, and non-runtime migration/history files.
- Secrets excluded: `.env`, credentials, API keys, tokens, caches, and datasets.
- Pre-freeze observations: the existing three predictions are retained but excluded from official frozen-V1 statistics.
