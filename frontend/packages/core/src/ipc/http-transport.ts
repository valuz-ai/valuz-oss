import { DEFAULT_SERVICES, type AgentEvent, type ServiceInfo } from '@valuz/shared'
import type { Transport } from './transport'

export class HttpTransport implements Transport {
  async invoke<T>(command: string): Promise<T> {
    if (command === 'get_services_status') {
      return DEFAULT_SERVICES as T
    }

    if (command === 'get_agent_events') {
      return [
        { type: 'thinking', content: 'HTTP transport is not connected yet.' },
        { type: 'text_done', content: 'Falling back to the web skeleton.' },
      ] as T
    }

    return { ok: true } as T
  }

  listen<T>(event: string, handler: (payload: T) => void): () => void {
    void event
    void handler
    return () => undefined
  }

  close(): void {}
}

export const getHttpTransportSeed = (): { services: ServiceInfo[]; events: AgentEvent[] } => ({
  services: DEFAULT_SERVICES,
  events: [
    { type: 'thinking', content: 'HTTP transport is not connected yet.' },
    { type: 'text_done', content: 'Falling back to the web skeleton.' },
  ],
})
