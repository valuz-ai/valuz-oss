import { describe, expect, it, vi } from 'vitest'
import { buildDesktopApi } from './desktop-api'

describe('buildDesktopApi', () => {
  it('exposes a valuzDesktop bridge with invoke and event helpers', async () => {
    const invoke = vi.fn().mockResolvedValue({ ok: true })
    const on = vi.fn()
    const off = vi.fn()

    const exposed = buildDesktopApi({
      runtime: {
        shell: 'electron',
        platform: 'darwin',
        version: '1.0.0',
      },
      invoke,
      on,
      off,
    })

    expect(exposed.runtime.shell).toBe('electron')
    expect(typeof exposed.invoke).toBe('function')
    expect(typeof exposed.on).toBe('function')
    expect(typeof exposed.off).toBe('function')
    await expect(exposed.invoke('desktop:get-shell-status')).resolves.toEqual({ ok: true })
  })
})
