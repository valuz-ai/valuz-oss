/**
 * Session-wide attachment cap. Local uploads and KB-sourced references
 * count together against this single budget. Mirrors the backend
 * ``settings.max_session_attachments`` (env: ``VALUZ_MAX_SESSION_ATTACHMENTS``).
 *
 * The backend is the source of truth and rejects requests past the cap
 * with a 400; this constant lets the desktop UI grey out the attachment
 * menu entries proactively so the user doesn't hit the error. If the two
 * ever drift, the backend 400 is the safety net — keep them in sync.
 */
export const MAX_SESSION_ATTACHMENTS = 20;
