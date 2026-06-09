import os

# The document parser offloads to a ``ProcessPoolExecutor`` in production
# (see ``valuz_agent.infra.parse_pool``). For the unit suite we force the
# in-thread / inline fallback so parse tests stay fast and don't spawn
# subprocesses (which re-import modules and slow CI). The dedicated offload
# regression test (``test_parse_pool_offload``) re-enables the pool explicitly.
os.environ.setdefault("VALUZ_PARSE_POOL_DISABLED", "1")
