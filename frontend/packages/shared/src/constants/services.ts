import type { ServiceInfo } from '../types/service'

export const DEFAULT_SERVICES: ServiceInfo[] = [
  { name: 'agent-server', status: 'running', port: 19100, pid: 4102, detail: 'Primary local agent runtime' },
]
