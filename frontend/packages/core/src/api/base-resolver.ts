/**
 * Per-entity API base resolution seam.
 *
 * OSS runs single-backend: every api module resolves against its module
 * ``_apiBase`` and this registry stays empty — zero behaviour change.
 *
 * Multi-target editions (commercial local + cloud-shared backend) register a
 * resolver that maps an entity reference (session / project / task / automation
 * / knowledge-base id) onto the base URL of the backend that owns it. Api
 * modules consult it for every entity-scoped call, so a conversation created on
 * the cloud backend keeps its whole chain (messages / SSE / attachments / queue
 * / actions) pinned to that backend while the rest of the app talks to the
 * default one — and likewise a cloud-created automation or knowledge base keeps
 * its get / edit / run / rescan calls on that backend.
 *
 * The resolver returning ``undefined`` means "no opinion" → module default.
 */

export interface ApiBaseRef {
  sessionId?: string;
  projectId?: string;
  taskId?: string;
  automationId?: string;
  playbookId?: string;
  kbId?: string;
}

export type ApiBaseResolver = (ref: ApiBaseRef) => string | undefined;

let _resolver: ApiBaseResolver | null = null;

/** Register (or clear with ``null``) the edition's entity→base resolver. */
export function setApiBaseResolver(resolver: ApiBaseResolver | null): void {
  _resolver = resolver;
}

/** Resolve the base URL for an entity-scoped call. */
export function resolveApiBase(ref: ApiBaseRef, fallback: string): string {
  if (_resolver === null) return fallback;
  try {
    return _resolver(ref) ?? fallback;
  } catch {
    return fallback;
  }
}
