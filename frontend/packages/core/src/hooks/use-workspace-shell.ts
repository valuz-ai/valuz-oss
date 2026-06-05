import { useEffect } from 'react'
import { useServices } from './use-services'
import { useAppStore } from '../store/app-store'

export const useWorkspaceShell = () => {
  const edition = useAppStore((state) => state.edition)
  const features = useAppStore((state) => state.features)
  const navItems = useAppStore((state) => state.navItems)
  const { services, setServices, transport } = useServices()

  useEffect(() => {
    let cancelled = false

    void transport.invoke<typeof services>('get_services_status').then((snapshot) => {
      if (!cancelled) {
        setServices(snapshot)
      }
    })

    const dispose = transport.listen<typeof services>('service-status-changed', (snapshot) => {
      if (!cancelled) {
        setServices(snapshot)
      }
    })

    return () => {
      cancelled = true
      dispose()
      transport.close()
    }
  }, [setServices, transport])

  return {
    edition,
    features,
    navItems,
    services,
    transport,
  }
}
