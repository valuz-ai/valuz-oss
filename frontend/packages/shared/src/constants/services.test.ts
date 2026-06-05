import { describe, expect, it } from 'vitest'
import { DEFAULT_SERVICES } from './services'

describe('default services', () => {
  it('exposes the personal runtime service list', () => {
    expect(DEFAULT_SERVICES).toEqual([
      { name: 'agent-server', status: 'running', port: 19100, pid: 4102, detail: 'Primary local agent runtime' },
    ])
  })
})
