import type { ServiceDescriptor } from '@valuz/core'
import { createServiceManager, type DesktopServiceManager } from '../services/mod'

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
}

type DesktopEventEmitter = (eventName: string, payload: unknown) => void

export const createDesktopRuntime = (
  manager: DesktopServiceManager,
  emitEvent: DesktopEventEmitter = () => undefined,
): DesktopRuntime => ({
  async startAllServices() {
    const snapshot = await manager.startAllServices()
    emitEvent('service-status-changed', snapshot)
    return snapshot
  },
  stopAllServices() {
    const snapshot = manager.stopAllServices()
    emitEvent('service-status-changed', snapshot)
    return snapshot
  },
  getServicesStatus() {
    return manager.getAllStatus()
  },
  async restartService(serviceName: string) {
    const snapshot = await manager.restartService(serviceName)
    emitEvent('service-status-changed', snapshot)
    return snapshot
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
})

export const createDesktopRuntimeForTest = () => createDesktopRuntime(createServiceManager())

export const serviceHandlers = (runtime: DesktopRuntime) => ({
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
})
