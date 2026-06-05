import { describe, expect, it } from 'vitest'
import { useServiceStore } from './service-store'

describe('service-store', () => {
  it('tracks services and the selected service', () => {
    useServiceStore.setState({
      services: [],
      selectedService: null,
    })

    useServiceStore.getState().setServices([
      { name: 'agent-server', status: 'running', port: 19100, pid: 10, detail: 'Runtime' },
    ])
    useServiceStore.getState().selectService('agent-server')

    const state = useServiceStore.getState()

    expect(state.services).toHaveLength(1)
    expect(state.selectedService).toBe('agent-server')
  })
})
