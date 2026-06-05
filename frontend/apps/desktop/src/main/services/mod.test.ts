import { describe, expect, it } from 'vitest'
import { createServiceManager } from './mod'

describe('createServiceManager', () => {
  it('initializes with a stopped agent-server service', () => {
    const manager = createServiceManager()
    const snapshot = manager.getAllStatus()

    expect(snapshot).toEqual([
      expect.objectContaining({
        name: 'agent-server',
        status: 'stopped',
        port: 19100,
      }),
    ])
  })
})
