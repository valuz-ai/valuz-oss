/**
 * Which backend a composer's attachment upload belongs to.
 *
 * A staged attachment row is backend-local: it exists only on the backend that
 * accepted the bytes, and the turn that ships it names its id. So the upload
 * has to land on the backend the turn will run on — if the two disagree the
 * id means nothing there, binding matches nothing, and the message goes out
 * without the file, silently.
 *
 * The three authorities, in order:
 *
 * 1. An existing conversation is pinned to the backend that owns it. The
 *    picker is locked at that point and does not describe this upload.
 * 2. A real project owns a backend; a draft in it follows the project.
 * 3. Otherwise the person's pick — 本地 / 云端 — decides.
 *
 * Step 2 is where this went wrong: ``chat-default`` is the 临时 sentinel that
 * bootstrap assigns to every quick chat, not a project id. Treating it as one
 * asked the resolver which backend owns a project that doesn't exist, got "no
 * opinion" back, and fell through to the module default — so every quick-chat
 * upload went to the LOCAL backend regardless of the selected service, in both
 * directions, while ``useConversationSend`` (which does classify the sentinel
 * as a chat) minted the session on the picked one.
 */

import type { ApiBaseRef } from "@valuz/core";
import { isRealProjectId } from "@valuz/shared";

export type ComposerUploadBaseParams = {
  /** The open conversation, or ``null`` for a draft. */
  selectedSessionId: string | null;
  /** May be the ``chat-default`` sentinel — that is not a project. */
  selectedProjectId: string | null;
  /** Base URL of the picked execution target, if this edition has any. */
  execTargetBaseUrl: string | undefined;
  /** Entity→backend resolver (``resolveApiBase``); "" means no opinion. */
  resolveBase: (ref: ApiBaseRef, fallback: string) => string;
};

export function resolveComposerUploadBase({
  selectedSessionId,
  selectedProjectId,
  execTargetBaseUrl,
  resolveBase,
}: ComposerUploadBaseParams): string | undefined {
  if (selectedSessionId) {
    return resolveBase({ sessionId: selectedSessionId }, "") || undefined;
  }
  if (isRealProjectId(selectedProjectId)) {
    return resolveBase({ projectId: selectedProjectId }, "") || undefined;
  }
  return execTargetBaseUrl;
}
