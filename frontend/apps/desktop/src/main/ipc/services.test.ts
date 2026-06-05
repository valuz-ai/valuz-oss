import { describe, expect, it } from 'vitest'
import { createDesktopRuntimeForTest } from './services'

describe('createDesktopRuntimeForTest', () => {
  it('starts required services and returns the updated snapshot', async () => {
    const runtime = createDesktopRuntimeForTest()

    const snapshot = await runtime.startAllServices()

    expect(snapshot[0]).toEqual(
      expect.objectContaining({
        name: 'agent-server',
        status: 'running',
      }),
    )
  })
})
