/**
 * Composer catalog seam.
 *
 * OSS owns the composer UI and its single-backend default. An edition may
 * install an adapter that interprets an opaque execution-target id and loads
 * the model and agent catalogs from the matching service. The seam deliberately
 * does not expose base URLs to composer consumers: target routing is edition
 * policy, not OSS UI behavior.
 */

import { agentsApi, type Agent } from "../api/agents-api";
import { providersApi, type LLMChannel } from "../api/providers-api";

export interface ComposerCatalogContext {
  /** Opaque edition-defined target id selected by the composer. */
  targetId?: string | null;
}

export interface ComposerCatalogAdapter {
  /** Stable identity used to synchronously discard results from another scope. */
  getScopeKey(context: ComposerCatalogContext): string;
  listAgents(
    context: ComposerCatalogContext,
  ): Promise<{ agents: Agent[] }>;
  listProviderChannels(
    context: ComposerCatalogContext,
  ): Promise<{ providers: LLMChannel[] }>;
}

const defaultAdapter: ComposerCatalogAdapter = {
  getScopeKey: () => "oss-default",
  listAgents: () => agentsApi.listAgents(undefined, { fresh: true }),
  listProviderChannels: () =>
    providersApi.list({ gated: true, fresh: true }),
};

let adapter: ComposerCatalogAdapter | null = null;

/** Register an edition adapter, or restore the OSS default with `null`. */
export function setComposerCatalogAdapter(
  next: ComposerCatalogAdapter | null,
): void {
  adapter = next;
}

/** Active edition adapter, falling back to the OSS single-backend behavior. */
export function getComposerCatalogAdapter(): ComposerCatalogAdapter {
  return adapter ?? defaultAdapter;
}
