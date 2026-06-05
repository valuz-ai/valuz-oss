import type { Transport } from './transport'

type DesktopInvoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>
type DesktopListener = (payload: unknown) => void

interface ValuzDesktopBridge {
  invoke: DesktopInvoke
  on: (event: string, handler: DesktopListener) => void
  off: (event: string, handler: DesktopListener) => void
}

const getGlobalWindow = () =>
  globalThis as typeof globalThis & {
    window?: {
      valuzDesktop?: ValuzDesktopBridge
    }
  }

const getDesktopBridge = (): ValuzDesktopBridge => {
  const bridge = getGlobalWindow().window?.valuzDesktop

  if (!bridge) {
    throw new Error('Electron desktop bridge is not available')
  }

  return bridge
}

export class ElectronTransport implements Transport {
  private readonly listeners = new Map<(payload: unknown) => void, DesktopListener>()

  async invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    return getDesktopBridge().invoke<T>(command, args)
  }

  listen<T>(event: string, handler: (payload: T) => void): () => void {
    const wrapped: DesktopListener = (payload) => {
      handler(payload as T)
    }

    this.listeners.set(handler as (payload: unknown) => void, wrapped)
    getDesktopBridge().on(event, wrapped)

    return () => {
      const registered = this.listeners.get(handler as (payload: unknown) => void)
      if (!registered) {
        return
      }

      getDesktopBridge().off(event, registered)
      this.listeners.delete(handler as (payload: unknown) => void)
    }
  }

  close(): void {
    this.listeners.clear()
  }
}
