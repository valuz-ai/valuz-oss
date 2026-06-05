export interface DesktopRuntimeInfo {
  shell: 'electron'
  platform: string
  version: string
}

export type DesktopInvoke = <T>(channel: string, payload?: Record<string, unknown>) => Promise<T>
export type DesktopEventHandler = (payload: unknown) => void

export interface DesktopApi {
  runtime: DesktopRuntimeInfo
  invoke: DesktopInvoke
  on: (event: string, handler: DesktopEventHandler) => void
  off: (event: string, handler: DesktopEventHandler) => void
}

export interface DesktopApiDependencies {
  runtime: DesktopRuntimeInfo
  invoke: DesktopInvoke
  on: (event: string, handler: DesktopEventHandler) => void
  off: (event: string, handler: DesktopEventHandler) => void
}

export const buildDesktopApi = (deps: DesktopApiDependencies): DesktopApi => ({
  runtime: deps.runtime,
  invoke: (channel, payload) => deps.invoke(channel, payload),
  on: (event, handler) => deps.on(event, handler),
  off: (event, handler) => deps.off(event, handler),
})
