"""Feedback — user outcome signals on assistant turns.

Rating (👍/👎 + reason), copy, and the server-emitted regenerate / fork /
share actions, one row per ``(user, message, action, block_ref)`` in the
host table ``valuz_feedback``. The persistence seam is
:class:`valuz_agent.ports.feedback.FeedbackPort`; this module owns the
request validation (``service``), the local datastore the OSS provider
writes through, and the best-effort server-side emit helper.
"""
