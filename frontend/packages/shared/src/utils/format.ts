import type { ServiceStatusType } from '../types/service'

export const formatStatusLabel = (status: ServiceStatusType): string =>
  status.charAt(0).toUpperCase() + status.slice(1)
