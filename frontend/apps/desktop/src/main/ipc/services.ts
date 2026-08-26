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
import {
  createNetworkEgressIpcHandlers,
  interruptActiveModelRuns,
  probeActiveModelRuns,
  reconfigureRuntimeEgress,
} from '@valuz/desktop-network-egress/main'
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

type ActiveRunsProbe = (port: number, token: string) => Promise<string[]>
type ActiveRunsInterrupt = (
  port: number,
  token: string,
  sessionIds: string[],
) => Promise<void>
type EgressRuntimeReconfigure = (
  port: number,
  token: string,
  bootstrap: EgressBootstrap | null,
  requiredUnavailable: boolean,
) => Promise<void>

export const createDesktopRuntime = (
  manager: DesktopServiceManager,
  emitEvent: DesktopEventEmitter = () => undefined,
  activeRunsProbe: ActiveRunsProbe = probeActiveModelRuns,
  activeRunsInterrupt: ActiveRunsInterrupt = interruptActiveModelRuns,
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
          const desktopToken = manager.getDesktopControlToken()
          const activeSessionIds = await activeRunsProbe(server.port, desktopToken)
          if (activeSessionIds.length > 0) {
            if (!options?.interruptActiveRuns) {
              throw new Error('egress_mode_change_blocked_by_active_runs')
            }
            try {
              await activeRunsInterrupt(server.port, desktopToken, activeSessionIds)
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
          } catch (error) {
            if (
              error instanceof Error &&
              error.message === 'egress_runtime_reconfigure_busy'
            ) {
              // A new/unwinding task won the race after confirmation. The
              // backend kept its previous network owner (409), so roll the
              // Electron owner and persisted choice back transactionally.
              status = await manager.setEgressMode(previous)
              emitEvent('egress-status-changed', status)
              throw new Error('egress_mode_change_blocked_by_active_runs')
            }
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
