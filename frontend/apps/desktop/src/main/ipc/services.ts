import type { ServiceDescriptor } from '@valuz/core'
import type {
  EgressBootstrap,
  EgressDiagnosticEvent,
  EgressManagerStatus,
  EgressMode,
  EgressSnapshot,
  RuntimePhaseRecord,
} from '@valuz/desktop-network-egress/contracts'
import { DEFAULT_NETWORK_EGRESS_POLICY } from '@valuz/desktop-network-egress/contracts'
import { createNetworkEgressIpcHandlers } from '@valuz/desktop-network-egress/main'
import type { NetworkEgressPolicy } from '@valuz/desktop-network-egress/contracts'
import { createServiceManager, type DesktopServiceManager } from '../services/mod'
import { cleanStaleUpdateCache } from '../update-cache'

// Once-per-session guard: purge a previous version's leftover update package the
// first time the backend reports healthy, not before. Keeping it until the new
// build proves it actually runs means a start-up failure (e.g. a bad update)
// still leaves the old package around to fall back to.
let updateCachePurged = false

export interface DesktopRuntime {
  startAllServices(): Promise<ReturnType<DesktopServiceManager['getAllStatus']>>
  stopAllServices(): ReturnType<DesktopServiceManager['stopAllServices']>
  getServicesStatus(): ReturnType<DesktopServiceManager['getAllStatus']>
  restartService(serviceName: string): Promise<ReturnType<DesktopServiceManager['getAllStatus']>>
  getServiceLogs(serviceName: string): string[]
  getAgentServerInfo(): ReturnType<DesktopServiceManager['getAgentServerInfo']>
  getShellStatus(): { ready: boolean }
  listServiceDescriptors(): ServiceDescriptor[]
  registerServiceDescriptor(descriptor: ServiceDescriptor): ServiceDescriptor
  unregisterServiceDescriptor(name: string): boolean
  getEgressDiagnostics(): EgressDiagnosticEvent[]
  getEgressSnapshots(): EgressSnapshot[]
  getEgressMode(): EgressMode
  getEgressStatus(): EgressManagerStatus
  getEgressRuntimePhases(): RuntimePhaseRecord[]
  setEgressMode(
    mode: EgressMode,
    options?: { interruptActiveRuns?: boolean },
  ): Promise<EgressManagerStatus>
}

type DesktopEventEmitter = (eventName: string, payload: unknown) => void

type ActiveRunsProbe = (port: number) => Promise<string[]>
type ActiveRunsInterrupt = (port: number, sessionIds: string[]) => Promise<void>
type EgressRuntimeReconfigure = (
  port: number,
  token: string,
  bootstrap: EgressBootstrap | null,
  requiredUnavailable: boolean,
) => Promise<void>

const probeActiveRuns: ActiveRunsProbe = async (port) => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1_500)
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/runs?status=running`, {
      signal: controller.signal,
    })
    if (!response.ok) return []
    const payload = (await response.json()) as {
      runs?: Array<{ session_id?: unknown }>
    }
    if (!Array.isArray(payload.runs)) return []
    return payload.runs.flatMap((run) =>
      typeof run.session_id === 'string' ? [run.session_id] : [],
    )
  } catch {
    // If the backend is already unavailable, switching network ownership may
    // be the recovery action that brings it back. Do not strand the user by
    // treating an unreachable activity probe as an active task.
    return []
  } finally {
    clearTimeout(timeout)
  }
}

const interruptActiveRuns: ActiveRunsInterrupt = async (port, sessionIds) => {
  await Promise.all(
    sessionIds.map(async (sessionId) => {
      const controller = new AbortController()
      // Runtime interruption can legitimately take close to a minute while a
      // model client unwinds. Give the backend enough time to finalize the
      // session as idle before rebuilding it under the new network owner.
      const timeout = setTimeout(() => controller.abort(), 70_000)
      try {
        const response = await fetch(
          `http://127.0.0.1:${port}/v1/sessions/${encodeURIComponent(sessionId)}/interrupt`,
          { method: 'POST', signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error(`interrupt_failed_${response.status}`)
        }
      } finally {
        clearTimeout(timeout)
      }
    }),
  )
}

const reconfigureRuntimeEgress: EgressRuntimeReconfigure = async (
  port,
  token,
  bootstrap,
  requiredUnavailable,
) => {
  const controller = new AbortController()
  // Rebuilding the most-recent Codex runtime can include its one-time cold
  // start. Keep the control request bounded but longer than a normal API call.
  const timeout = setTimeout(() => controller.abort(), 120_000)
  try {
    const body = JSON.stringify({
      bootstrap,
      required_unavailable: requiredUnavailable,
      prewarm_limit: 1,
    })
    const activeDrainDeadline = Date.now() + 5_000
    while (true) {
      const response = await fetch(
        `http://127.0.0.1:${port}/v1/system/network-egress`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-valuz-desktop-token': token,
          },
          body,
          signal: controller.signal,
        },
      )
      if (response.ok) return
      // The interrupt response can win a short race with the kernel turn's
      // final cleanup. Let that task drain before falling back to a restart.
      if (response.status === 409 && Date.now() < activeDrainDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 100))
        continue
      }
      throw new Error(`egress_runtime_reconfigure_failed_${response.status}`)
    }
  } finally {
    clearTimeout(timeout)
  }
}

export const createDesktopRuntime = (
  manager: DesktopServiceManager,
  emitEvent: DesktopEventEmitter = () => undefined,
  activeRunsProbe: ActiveRunsProbe = probeActiveRuns,
  activeRunsInterrupt: ActiveRunsInterrupt = interruptActiveRuns,
  egressRuntimeReconfigure: EgressRuntimeReconfigure = reconfigureRuntimeEgress,
): DesktopRuntime => {
  const restartService = async (serviceName: string) => {
    // Tell the renderer *before* stopping a service: otherwise the routed app
    // remains interactive during the short restart window and a send can
    // race the closed loopback port, surfacing a misleading permanent
    // "backend unavailable" turn even though the replacement is healthy a
    // moment later.
    const restarting = manager.getAllStatus().map((service) =>
      service.name === serviceName
        ? { ...service, status: 'starting' as const, pid: null }
        : service,
    )
    emitEvent('service-status-changed', restarting)

    const snapshot = await manager.restartService(serviceName)
    emitEvent('service-status-changed', snapshot)
    return snapshot
  }

  return {
    async startAllServices() {
      const snapshot = await manager.startAllServices()
      emitEvent('service-status-changed', snapshot)
      // Backend came up healthy → the app has truly started. Only now purge a
      // previous version's leftover update package (see ``cleanStaleUpdateCache``).
      if (
        !updateCachePurged &&
        snapshot.some((s) => s.name === 'agent-server' && s.status === 'running')
      ) {
        updateCachePurged = true
        cleanStaleUpdateCache()
      }
      return snapshot
    },
    async stopAllServices() {
      const snapshot = await manager.stopAllServices()
      emitEvent('service-status-changed', snapshot)
      return snapshot
    },
    getServicesStatus() {
      return manager.getAllStatus()
    },
    async restartService(serviceName: string) {
      return restartService(serviceName)
    },
    getServiceLogs(serviceName: string) {
      return manager.getLogs(serviceName)
    },
    getAgentServerInfo() {
      return manager.getAgentServerInfo()
    },
    getShellStatus() {
      return manager.getShellStatus()
    },
    listServiceDescriptors() {
      return manager.descriptors.snapshot()
    },
    registerServiceDescriptor(descriptor: ServiceDescriptor) {
      const registered = manager.registerDescriptor(descriptor)
      emitEvent('service-descriptors-changed', manager.descriptors.snapshot())
      return registered
    },
    unregisterServiceDescriptor(name: string) {
      const removed = manager.unregisterDescriptor(name)
      if (removed) {
        emitEvent('service-descriptors-changed', manager.descriptors.snapshot())
      }
      return removed
    },
    getEgressDiagnostics() {
      return manager.getEgressDiagnostics()
    },
    getEgressSnapshots() {
      return manager.getEgressSnapshots()
    },
    getEgressMode() {
      return manager.getEgressMode()
    },
    getEgressStatus() {
      return manager.getEgressStatus()
    },
    getEgressRuntimePhases() {
      return manager.getEgressRuntimePhases()
    },
    async setEgressMode(mode, options) {
      const previous = manager.getEgressMode()
      const crossesOwnershipBoundary = (previous === 'off') !== (mode === 'off')
      if (crossesOwnershipBoundary) {
        const server = manager.getAgentServerInfo()
        if (server.status === 'running') {
          const activeSessionIds = await activeRunsProbe(server.port)
          if (activeSessionIds.length > 0) {
            if (!options?.interruptActiveRuns) {
              throw new Error('egress_mode_change_blocked_by_active_runs')
            }
            try {
              await activeRunsInterrupt(server.port, activeSessionIds)
            } catch {
              throw new Error('egress_mode_change_interrupt_failed')
            }
          }
        }
      }
      let status: EgressManagerStatus
      try {
        status = await manager.setEgressMode(mode)
      } catch (error) {
        if (previous === 'off' && manager.getEgressMode() !== 'off') {
          const server = manager.getAgentServerInfo()
          const desktopToken = manager.getDesktopControlToken()
          if (server.status === 'running') {
            try {
              await egressRuntimeReconfigure(server.port, desktopToken, null, true)
            } catch {
              // Compatibility fallback for an older/unhealthy backend that
              // does not expose the live runtime-control endpoint.
              await restartService('agent-server')
            }
          }
        }
        emitEvent('egress-status-changed', manager.getEgressStatus())
        throw error
      }
      if (crossesOwnershipBoundary) {
        const server = manager.getAgentServerInfo()
        const desktopToken = manager.getDesktopControlToken()
        if (server.status === 'running') {
          try {
            await egressRuntimeReconfigure(
              server.port,
              desktopToken,
              manager.getEgressBootstrap?.() ?? null,
              // `enabled` means the desktop capability/frontends are
              // available, not that Valuz currently owns model networking.
              // In `off`, a null bootstrap is the intended independent-client
              // configuration and must never become the fail-loud marker.
              status.enabled && status.mode !== 'off' && !status.started,
            )
          } catch {
            // Fail safe and preserve compatibility with a backend from before
            // live reconfiguration. The normal same-version path never
            // restarts the service.
            await restartService('agent-server')
          }
        }
      }
      emitEvent('egress-status-changed', status)
      return status
    },
  }
}

export const createDesktopRuntimeForTest = () => createDesktopRuntime(createServiceManager())

export const serviceHandlers = (
  runtime: DesktopRuntime,
  networkEgressPolicy: NetworkEgressPolicy = DEFAULT_NETWORK_EGRESS_POLICY,
) => ({
  get_services_status: () => runtime.getServicesStatus(),
  start_all_services: () => runtime.startAllServices(),
  stop_all_services: () => runtime.stopAllServices(),
  restart_service: (_: unknown, payload?: { serviceName?: string }) =>
    runtime.restartService(payload?.serviceName ?? ''),
  get_service_logs: (_: unknown, payload?: { serviceName?: string }) =>
    runtime.getServiceLogs(payload?.serviceName ?? ''),
  get_agent_server_info: () => runtime.getAgentServerInfo(),
  desktop_shell_status: () => runtime.getShellStatus(),
  list_service_descriptors: () => runtime.listServiceDescriptors(),
  register_service_descriptor: (_: unknown, payload?: { descriptor?: ServiceDescriptor }) =>
    runtime.registerServiceDescriptor(payload?.descriptor as ServiceDescriptor),
  unregister_service_descriptor: (_: unknown, payload?: { name?: string }) =>
    runtime.unregisterServiceDescriptor(payload?.name ?? ''),
  ...createNetworkEgressIpcHandlers(runtime, networkEgressPolicy),
})
