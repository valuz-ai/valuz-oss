import { useCallback, useEffect, useState } from 'react'

import type { Agent } from '../api/agents-api'
import { getComposerCatalogAdapter } from '../edition/composer-catalog'
import { useAgentStore } from '../store/agent-store'

export const useAgent = () => useAgentStore()

export interface ComposerAgentLibrary {
  agents: Agent[]
  loaded: boolean
  /** The last attempt failed, so an empty roster proves nothing. */
  failed: boolean
  /**
   * An empty/failed answer is still being re-asked. ``loaded`` keeps its plain
   * meaning (a response arrived) so pickers render immediately; only a caller
   * about to tell the user nothing is configured should wait for this to clear.
   */
  settling: boolean
  /** Re-ask now — for a retry the user asked for. */
  refresh: () => void
}

interface ComposerAgentLibraryState
  extends Omit<ComposerAgentLibrary, 'settling' | 'refresh'> {
  requestKey: string
  attempt: number
}

// An empty roster shortly after login usually means "not seeded yet" rather
// than "none": the built-in agent is installed by a post-login step that first
// resolves its model against the owner's remote catalog. Callers use this
// roster to tell the user nothing is configured, so trusting the first empty
// answer puts a false claim on screen that only clears if the user happens to
// navigate away and back. Re-ask a few times before settling, and again
// whenever the window regains focus.
const EMPTY_RETRIES = 4
const EMPTY_RETRY_BASE_MS = 800

/**
 * Load the temporary-conversation agent library through the active edition's
 * catalog adapter. OSS treats ``targetId`` as opaque; only an installed edition
 * adapter may interpret it. Scope changes synchronously hide the previous
 * roster, and cleanup prevents an obsolete response from replacing the active
 * scope's agents.
 */
export function useComposerAgentLibrary(
  targetId?: string | null,
  refreshKey?: string | number | null,
): ComposerAgentLibrary {
  const adapter = getComposerCatalogAdapter()
  const scopeKey = adapter.getScopeKey({ targetId })
  const requestKey = `${scopeKey}\u0000${refreshKey ?? ''}`
  const [state, setState] = useState<ComposerAgentLibraryState>({
    requestKey,
    agents: [],
    loaded: false,
    failed: false,
    attempt: 0,
  })
  // Bumped by the retry timer, the focus listener and refresh(). All mean "ask
  // again for the same scope", which requestKey alone cannot express.
  const [reload, setReload] = useState(0)
  const refresh = useCallback(() => setReload((n) => n + 1), [])

  useEffect(() => {
    let active = true
    const settle = (agents: Agent[], failed: boolean) =>
      setState((prev) => ({
        requestKey,
        agents,
        loaded: true,
        failed,
        attempt: prev.requestKey === requestKey ? prev.attempt + 1 : 1,
      }))

    void adapter
      .listAgents({ targetId })
      .then(({ agents }) => {
        if (active) settle(agents, false)
      })
      .catch(() => {
        if (active) settle([], true)
      })

    return () => {
      active = false
    }
  }, [adapter, requestKey, targetId, reload])

  const settled = state.requestKey === requestKey && state.loaded
  const inconclusive = settled && (state.failed || state.agents.length === 0)
  const retrying = inconclusive && state.attempt < EMPTY_RETRIES

  useEffect(() => {
    if (!retrying) return
    const timer = setTimeout(
      () => setReload((n) => n + 1),
      EMPTY_RETRY_BASE_MS * state.attempt,
    )
    return () => clearTimeout(timer)
  }, [retrying, state.attempt])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const refresh = () => setReload((n) => n + 1)
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [])

  if (state.requestKey !== requestKey) {
    return {
      agents: [],
      loaded: false,
      failed: false,
      settling: false,
      refresh,
    }
  }
  return {
    agents: state.agents,
    loaded: state.loaded,
    failed: state.failed,
    settling: retrying,
    refresh,
  }
}
