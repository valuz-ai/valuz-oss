"""Feedback module errors (module code 92)."""

from __future__ import annotations

from valuz_agent.infra.errors import ConflictError, NotFoundError, UnprocessableEntityError


class FeedbackMessageNotFound(NotFoundError):
    error_code = 404_921
    message = "Message not found in this session"


class FeedbackMessageRunning(ConflictError):
    error_code = 409_921
    message = "Message is still running; feedback is accepted once the turn ends"


class FeedbackInvalid(UnprocessableEntityError):
    error_code = 422_921
    message = "Invalid feedback payload"
