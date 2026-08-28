/**
 * The id a conversation carries when it belongs to no project — a 临时 /
 * quick chat. Bootstrap assigns it to every draft that wasn't opened from a
 * project, and the backend stores it on the session row.
 *
 * It looks like a project id and is not one: no project row exists under it,
 * so anything that answers questions ABOUT a project — who owns it, which
 * backend it lives on, what its file tree is — has no answer for this value.
 *
 * Named because the literal was compared in a handful of places and one of
 * them got it wrong: the composer's upload path treated the sentinel as a
 * real project, asked the base resolver which backend owns it, received "no
 * opinion", and quietly fell through to the module default. Every quick-chat
 * attachment went to the local backend regardless of the selected service,
 * while the turn itself was created on the selected one — so the file and its
 * message landed on different backends and the file was dropped.
 */
export const CHAT_DEFAULT_PROJECT_ID = "chat-default";

/** True for anything that is not a real project — ``null`` or the sentinel. */
export function isRealProjectId(
  projectId: string | null | undefined,
): projectId is string {
  return Boolean(projectId) && projectId !== CHAT_DEFAULT_PROJECT_ID;
}
