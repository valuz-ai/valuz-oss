export type ServiceStatusType = 'stopped' | 'starting' | 'running' | 'error'

export interface ServiceInfo {
  name: string
  status: ServiceStatusType
  port: number | null
  pid: number | null
  detail?: string
}

export interface CraftServerInfo {
  port: number
  status: ServiceStatusType
  token?: string | null
}
