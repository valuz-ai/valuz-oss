/** Browser-level invalidation contract for resources changed outside the
 * currently rendered surface (for example, a confirmation card in chat).
 *
 * Producers publish only after the persistence call succeeds. Consumers own
 * their refetch policy and should filter by both resourceType and projectId.
 */
export const RESOURCE_REFRESH_EVENT = "valuz:resource-refresh";

export interface ResourceRefreshDetail {
  resourceType: string;
  projectId?: string | null;
  resourceId?: string | null;
}

export type ResourceRefreshEvent = CustomEvent<ResourceRefreshDetail>;

export function notifyResourceRefresh(detail: ResourceRefreshDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(RESOURCE_REFRESH_EVENT, { detail }));
}
