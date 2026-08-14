# References

Exploration notes on external systems we integrate with or evaluate. Each
subdirectory covers one system: what it is, the interface we would consume,
verified behavior (from real runs, not just docs), and the gap analysis
against our own contracts.

These are **engineering references**, not product docs — they capture the
state of an external system at the time of exploration and are dated
accordingly. Re-verify before relying on a claim after the upstream moves.

| Directory | System | Status |
|-----------|--------|--------|
| [deepseek-harness/](deepseek-harness/README.md) | DeepSeek Harness (`dsh`) — candidate 4th kernel runtime via its Python SDK | Explored 2026-08-13 against `0.1.0-rc.5` |
