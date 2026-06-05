import { useMemo } from 'react'
import { createTransport } from '../ipc/transport'
import { useServiceStore } from '../store/service-store'

export const useServices = () => {
  const services = useServiceStore((state) => state.services)
  const selectedService = useServiceStore((state) => state.selectedService)
  const setServices = useServiceStore((state) => state.setServices)
  const selectService = useServiceStore((state) => state.selectService)
  const transport = useMemo(() => createTransport(), [])

  return {
    services,
    selectedService,
    setServices,
    selectService,
    transport,
  }
}
