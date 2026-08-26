# Conversation history loading stability

## Problem

Opening an existing conversation can briefly show the no-model empty state and
then leave the transcript blank. Provider discovery currently represents
loading, failure, and a successful empty response as the same empty array. The
conversation page uses that array to replace the entire transcript. Concurrent
route bootstraps can also resolve out of order, and a failed history hydration
can be skipped merely because the selected session id did not change.

## Design

Keep the existing array-returning provider hook for compatibility, and add a
state-returning hook that exposes `loading`, `ready`, and `error`. An existing
conversation always renders its transcript; the no-model empty state is only
valid for a new conversation after provider discovery completed successfully
with zero enabled channels.

Guard the conversation bootstrap with a latest-request token. Route changes
invalidate the previous token before its async work can commit state. Track the
session whose history completed hydration successfully and skip a same-session
refresh only when that exact session is already hydrated. Failed hydration is
therefore retryable without a hard refresh. Use the route session id as the
provider-target source while session detail is loading, and treat a repeated
navigation to the same history URL as an explicit bootstrap retry.

## Verification

- Provider hook tests cover initial loading, target switches, failures, and
  successful empty responses.
- Conversation state tests cover the no-model gate, history retry decision, and
  out-of-order bootstrap invalidation.
- Run the focused Vitest files, frontend typecheck, and lint.
