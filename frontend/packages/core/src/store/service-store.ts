import { create } from 'zustand'
import { DEFAULT_SERVICES, type ServiceInfo } from '@valuz/shared'

interface ServiceStoreState {
  services: ServiceInfo[]
  selectedService: string | null
  setServices: (services: ServiceInfo[]) => void
  selectService: (serviceName: string | null) => void
}

export const useServiceStore = create<ServiceStoreState>((set, get) => ({
  services: DEFAULT_SERVICES,
  selectedService: DEFAULT_SERVICES[0]?.name ?? null,
  setServices: (services) =>
    set((state) => {
      const nextSelected =
        state.selectedService && services.some((service) => service.name === state.selectedService)
          ? state.selectedService
          : services[0]?.name ?? null

      return {
        services,
        selectedService: nextSelected,
      }
    }),
  selectService: (serviceName) => {
    const services = get().services
    const nextSelected =
      serviceName && services.some((service) => service.name === serviceName) ? serviceName : null

    set({ selectedService: nextSelected })
  },
}))
