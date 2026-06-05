import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ServiceDescriptor } from '../edition/profile'
import { useRegistryStore } from '../edition/registry-store'
import { createTransport } from '../ipc/transport'

interface UseServiceDescriptorsResult {
  descriptors: ServiceDescriptor[]
  registerRemote: (descriptor: ServiceDescriptor) => Promise<ServiceDescriptor>
  unregisterRemote: (name: string) => Promise<boolean>
  refresh: () => Promise<void>
}

/**
 * Unified view over the TS registry store and the Electron-side descriptor
 * registry. Local (plugin) registrations flow through `useRegistryStore`;
 * `registerRemote` / `unregisterRemote` call into the desktop backend and
 * mirror the change locally.
 */
export const useServiceDescriptors = (): UseServiceDescriptorsResult => {
  const transport = useMemo(() => createTransport(), [])
  const localDescriptors = useRegistryStore((state) => state.services)
  const registerLocal = useRegistryStore((state) => state.registerService)
  const unregisterLocal = useRegistryStore((state) => state.unregisterService)

  const [remoteDescriptors, setRemoteDescriptors] = useState<ServiceDescriptor[]>([])

  const refresh = useCallback(async () => {
    try {
      const snapshot = await transport.invoke<ServiceDescriptor[]>('list_service_descriptors')
      setRemoteDescriptors(snapshot)
    } catch {
      // Non-desktop environments (webui) return no remote descriptors.
      setRemoteDescriptors([])
    }
  }, [transport])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async transport.fetch on mount
    void refresh()
    const dispose = transport.listen<ServiceDescriptor[]>('service-descriptors-changed', (snapshot) => {
      setRemoteDescriptors(snapshot)
    })
    return () => {
      dispose()
    }
  }, [refresh, transport])

  const descriptors = useMemo(() => {
    const merged = new Map<string, ServiceDescriptor>()
    for (const item of localDescriptors) {
      merged.set(item.name, item)
    }
    for (const item of remoteDescriptors) {
      merged.set(item.name, item)
    }
    return Array.from(merged.values())
  }, [localDescriptors, remoteDescriptors])

  const registerRemote = useCallback(
    async (descriptor: ServiceDescriptor) => {
      const result = await transport.invoke<ServiceDescriptor>('register_service_descriptor', {
        descriptor,
      })
      registerLocal(result)
      return result
    },
    [registerLocal, transport],
  )

  const unregisterRemote = useCallback(
    async (name: string) => {
      const removed = await transport.invoke<boolean>('unregister_service_descriptor', { name })
      if (removed) {
        unregisterLocal(name)
      }
      return removed
    },
    [transport, unregisterLocal],
  )

  return { descriptors, registerRemote, unregisterRemote, refresh }
}
