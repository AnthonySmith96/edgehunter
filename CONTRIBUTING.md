# Contributing

EdgeHunter welcomes fixes that make the evidence more reproducible, the execution model more realistic, or the risk controls harder to bypass.

1. Fork the repository and create a focused branch.
2. Keep credentials and local state out of commits. Run `python scripts/check_public_release.py` before pushing.
3. Install the locked development environment with `uv sync --frozen --extra dev --native-tls`.
4. Run `python -m pytest -q`, `python -m mypy src`, and `ruff check src tests scripts deploy`.
5. Describe the trigger, behavior change, and evidence. Report losing or inconclusive results as clearly as winning ones.

Changes to frozen experiments must create a new experiment identity. Do not rewrite historical reports, retune on their holdout, or present simulated balances as money. New provider calls need an explicit budget, timeout, durable accounting, and a failure mode that abstains.

Useful areas include calibration, market-resolution adapters, fee and fill realism, Linux/macOS onboarding, dataset manifests, and report visualization.
