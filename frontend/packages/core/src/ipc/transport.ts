import type { AgentEvent, ServiceInfo } from '@valuz/shared'
import { ElectronTransport } from './electron-transport'
import { HttpTransport, getHttpTransportSeed } from './http-transport'

export interface Transport {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>
  listen<T>(event: string, handler: (payload: T) => void): () => void
  close(): void
}

interface MockState {
  readonly services: ServiceInfo[]
  readonly events: AgentEvent[]
}

const createMockState = (): MockState => ({
  services: getHttpTransportSeed().services,
  events: [
    ...getHttpTransportSeed().events,
    { type: 'thinking', content: 'Mock transport is warming up the workspace shell.' },
    { type: 'text_done', content: 'Workspace shell ready.' },
  ],
})

export class MockTransport implements Transport {
  private readonly listeners = new Map<string, Set<(payload: unknown) => void>>()

  async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    const state = createMockState()
    void args

    if (command === 'get_services_status') {
      return state.services as T
    }

    if (command === 'get_agent_events') {
      return state.events as T
    }

    return { command, ok: true } as T
  }

  listen<T>(event: string, handler: (payload: T) => void): () => void {
    const handlers = this.listeners.get(event) ?? new Set<(payload: unknown) => void>()
    handlers.add(handler as (payload: unknown) => void)
    this.listeners.set(event, handlers)

    return () => {
      const currentHandlers = this.listeners.get(event)
      currentHandlers?.delete(handler as (payload: unknown) => void)
    }
  }

  close(): void {
    this.listeners.clear()
  }
}

const isElectronRuntime = () => {
  const globalWindow = globalThis as typeof globalThis & {
    window?: {
      valuzDesktop?: unknown
    }
  }

  return typeof globalWindow.window !== 'undefined' && 'valuzDesktop' in globalWindow.window
}

export const createTransport = (): Transport => {
  if (isElectronRuntime()) {
    return new ElectronTransport()
  }

  return new HttpTransport()
}
