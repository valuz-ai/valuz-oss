"""One-time setup-job framework for parser plugins.

A setup job represents an explicit, user-authorized side-effect that must
run before a plugin capability becomes usable. Today the only one is
``RapidOcrSetupJob`` (PP-OCRv5 model download from ModelScope); future
cloud plugins use the same framework for things like license acceptance
gates.

Three pieces:

- ``SetupJob`` — the per-setup-id worker contract (``run`` + ``progress``).
- ``SetupJobController`` — process-wide singleton that owns the daemon
  threads, cancel events, and DB writes. Routes call into the controller;
  the controller never blocks the request path.
- Concrete jobs live in their own modules: ``setup_jobs/rapidocr.py`` etc.
"""

from valuz_agent.modules.parser.setup_jobs.base import (
    SetupJob,
    SetupJobAlreadyRunning,
    SetupJobController,
    SetupJobNotFound,
    SetupJobStatus,
    build_default_setup_controller,
)
from valuz_agent.modules.parser.setup_jobs.rapidocr import (
    RAPIDOCR_SETUP_ID,
    RapidOcrSetupJob,
)

__all__ = [
    "RAPIDOCR_SETUP_ID",
    "RapidOcrSetupJob",
    "SetupJob",
    "SetupJobAlreadyRunning",
    "SetupJobController",
    "SetupJobNotFound",
    "SetupJobStatus",
    "build_default_setup_controller",
]
