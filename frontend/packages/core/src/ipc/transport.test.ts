import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createTransport } from './transport'

const electronInvokeMock = vi.fn<
  (command: string, args?: Record<string, unknown>) => Promise<unknown>
>()
const electronOnMock = vi.fn()
const electronOffMock = vi.fn()

describe('createTransport', () => {
  beforeEach(() => {
    electronInvokeMock.mockReset()
    electronOnMock.mockReset()
    electronOffMock.mockReset()
    delete (window as Window & { valuzDesktop?: unknown }).valuzDesktop
  })

  it('should fall back to HttpTransport when Electron bridge is not available', async () => {
    const transport = createTransport()

    await expect(transport.invoke('get_services_status')).resolves.toEqual([
      { name: 'agent-server', status: 'running', port: 19100, pid: 4102, detail: 'Primary local agent runtime' },
    ])
  })

  it('should use ElectronTransport when valuzDesktop bridge is present', async () => {
    ;(window as Window & { valuzDesktop?: unknown }).valuzDesktop = {
      invoke: electronInvokeMock,
      on: electronOnMock,
      off: electronOffMock,
      runtime: { shell: 'electron', platform: 'darwin', version: '1.0.0' },
    }
    electronInvokeMock.mockResolvedValue([{ name: 'Craft Server', status: 'running', port: 19100, pid: 9001 }])

    const transport = createTransport()

    await expect(transport.invoke('get_services_status')).resolves.toEqual([
      { name: 'Craft Server', status: 'running', port: 19100, pid: 9001 },
    ])
    expect(electronInvokeMock).toHaveBeenCalledWith('get_services_status', undefined)
  })
})
