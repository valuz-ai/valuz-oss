"""Allow ``python -m valuz_agent`` to dispatch to ``valuz_agent.main``."""

from valuz_agent.main import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
