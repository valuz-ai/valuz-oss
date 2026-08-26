import { describe, expect, it, vi } from 'vitest'
import {
  DESKTOP_CAPABILITIES_CHANNEL,
  NETWORK_EGRESS_CHANNELS,
} from '@valuz/desktop-network-egress/contracts'
import {
  createDesktopRuntime,
  createDesktopRuntimeForTest,
  serviceHandlers,
} from './services'
import { DescriptorRegistry } from '../services/descriptors'

describe('createDesktopRuntimeForTest', () => {
  it('registers the complete versioned network egress IPC surface', () => {
    const handlers = serviceHandlers(createDesktopRuntimeForTest())

    expect(Object.keys(handlers)).toEqual(
      expect.arrayContaining([
        DESKTOP_CAPABILITIES_CHANNEL,
        ...Object.values(NETWORK_EGRESS_CHANNELS),
      ]),
    )
  })

  it('starts required services and returns the updated snapshot', async () => {
    const runtime = createDesktopRuntime({
      descriptors: new DescriptorRegistry([]),
      startAllServices: async () => [
        {
          name: 'agent-server',
          status: 'running',
          port: 19100,
          pid: 123,
          detail: 'Ready',
        },
      ],
      stopAllServices: async () => [],
      restartService: async () => [],
      getLogs: () => [],
      getAgentServerInfo: () => ({
        port: 19100,
        status: 'running',
        token: 'test-token',
      }),
      getDesktopControlToken: () => 'desktop-control-token',
      getShellStatus: () => ({ ready: true }),
      getAllStatus: () => [],
      registerDescriptor: (descriptor) => descriptor,
      unregisterDescriptor: () => true,
      getEgressDiagnostics: () => [],
      getEgressSnapshots: () => [],
      getEgressMode: () => 'off',
      getEgressStatus: () => ({
        mode: 'off',
        enabled: false,
        started: false,
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }),
      getEgressRuntimePhases: () => [],
      setEgressMode: async () => ({
        mode: 'off',
        enabled: false,
        started: false,
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }),
    })

    const snapshot = await runtime.startAllServices()

    expect(snapshot[0]).toEqual(
      expect.objectContaining({
        name: 'agent-server',
        status: 'running',
      }),
    )
  })

  it('reconfigures runtimes without restarting the sidecar across the ownership boundary', async () => {
    let mode: 'auto' | 'direct' | 'off' = 'off'
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const reconfigure = vi.fn(async () => undefined)
    const emitEvent = vi.fn()
    const status = () => ({
      mode,
      // Mirrors EgressManagerStatus: the feature remains available while the
      // selected ownership mode is `off`.
      enabled: true,
      started: mode !== 'off',
      emergencyOverride: false,
      snapshotCount: 0,
      diagnosticEventCount: 0,
    })
    const runtime = createDesktopRuntime(
      {
        descriptors: new DescriptorRegistry([]),
        startAllServices: async () => [],
        stopAllServices: async () => [],
        restartService,
        getLogs: () => [],
        getAgentServerInfo: () => ({
          port: 19100,
          status: 'running',
          token: 'test-token',
        }),
        getDesktopControlToken: () => 'desktop-control-token',
        getShellStatus: () => ({ ready: true }),
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => mode,
        getEgressStatus: status,
        getEgressRuntimePhases: () => [],
        getEgressBootstrap: () =>
          mode === 'off'
            ? null
            : {
                mode,
                controlEndpoint: 'http://127.0.0.1:43123',
                bootstrapToken: 'x'.repeat(43),
                expiresAt: Date.now() + 60_000,
              },
        setEgressMode: async (nextMode) => {
          mode = nextMode
          return status()
        },
      },
      emitEvent,
      async () => [],
      undefined,
      reconfigure,
    )

    await runtime.setEgressMode('auto')
    await runtime.setEgressMode('direct')
    await runtime.setEgressMode('off')

    expect(restartService).not.toHaveBeenCalled()
    expect(reconfigure).toHaveBeenCalledTimes(2)
    expect(reconfigure).toHaveBeenNthCalledWith(
      1,
      19100,
      'desktop-control-token',
      expect.objectContaining({ mode: 'auto' }),
      false,
    )
    expect(reconfigure).toHaveBeenNthCalledWith(
      2,
      19100,
      'desktop-control-token',
      null,
      false,
    )
    expect(emitEvent.mock.calls).toEqual([
      ['egress-status-changed', expect.objectContaining({ mode: 'auto' })],
      ['egress-status-changed', expect.objectContaining({ mode: 'direct' })],
      ['egress-status-changed', expect.objectContaining({ mode: 'off' })],
    ])
  })

  it('does not change network ownership while a model task is running', async () => {
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const setEgressMode = vi.fn(async () => ({
      mode: 'off' as const,
      enabled: false,
      started: false,
      emergencyOverride: false,
      snapshotCount: 0,
      diagnosticEventCount: 0,
    }))
    const runtime = createDesktopRuntime(
      {
        descriptors: new DescriptorRegistry([]),
        startAllServices: async () => [],
        stopAllServices: async () => [],
        restartService,
        getLogs: () => [],
        getAgentServerInfo: () => ({
          port: 19100,
          status: 'running',
          token: 'test-token',
        }),
        getDesktopControlToken: () => 'desktop-control-token',
        getShellStatus: () => ({ ready: true }),
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => 'auto',
        getEgressStatus: () => ({
          mode: 'auto',
          enabled: true,
          started: true,
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }),
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      undefined,
      async () => ['active-session'],
    )

    await expect(runtime.setEgressMode('off')).rejects.toThrow(
      'egress_mode_change_blocked_by_active_runs',
    )
    expect(setEgressMode).not.toHaveBeenCalled()
    expect(restartService).not.toHaveBeenCalled()
  })

  it('interrupts active tasks before changing network ownership when confirmed', async () => {
    let mode: 'auto' | 'direct' | 'off' = 'auto'
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const interrupt = vi.fn(async () => undefined)
    const reconfigure = vi.fn(async () => undefined)
    const setEgressMode = vi.fn(async (nextMode: typeof mode) => {
      mode = nextMode
      return {
        mode,
        enabled: mode !== 'off',
        started: mode !== 'off',
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }
    })
    const runtime = createDesktopRuntime(
      {
        descriptors: new DescriptorRegistry([]),
        startAllServices: async () => [],
        stopAllServices: async () => [],
        restartService,
        getLogs: () => [],
        getAgentServerInfo: () => ({
          port: 19100,
          status: 'running',
          token: 'test-token',
        }),
        getDesktopControlToken: () => 'desktop-control-token',
        getShellStatus: () => ({ ready: true }),
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => mode,
        getEgressStatus: () => ({
          mode,
          enabled: mode !== 'off',
          started: mode !== 'off',
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }),
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      undefined,
      async () => ['session-a', 'session-b'],
      interrupt,
      reconfigure,
    )

    await expect(
      runtime.setEgressMode('off', { interruptActiveRuns: true }),
    ).resolves.toMatchObject({ mode: 'off' })
    expect(interrupt).toHaveBeenCalledWith(
      19100,
      'desktop-control-token',
      ['session-a', 'session-b'],
    )
    expect(interrupt.mock.invocationCallOrder[0]).toBeLessThan(
      setEgressMode.mock.invocationCallOrder[0],
    )
    expect(reconfigure).toHaveBeenCalledWith(
      19100,
      'desktop-control-token',
      null,
      false,
    )
    expect(restartService).not.toHaveBeenCalled()
  })

  it('rolls the owner choice back instead of restarting when a new task wins the race', async () => {
    let mode: 'auto' | 'direct' | 'off' = 'auto'
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const setEgressMode = vi.fn(async (nextMode: typeof mode) => {
      mode = nextMode
      return {
        mode,
        enabled: true,
        started: mode !== 'off',
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }
    })
    const restartService = vi.fn(async () => [runningService])
    const emitEvent = vi.fn()
    const runtime = createDesktopRuntime(
      {
        descriptors: new DescriptorRegistry([]),
        startAllServices: async () => [],
        stopAllServices: async () => [],
        restartService,
        getLogs: () => [],
        getAgentServerInfo: () => ({
          port: 19100,
          status: 'running',
          token: 'test-token',
        }),
        getDesktopControlToken: () => 'desktop-control-token',
        getShellStatus: () => ({ ready: true }),
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => mode,
        getEgressStatus: () => ({
          mode,
          enabled: true,
          started: mode !== 'off',
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }),
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      emitEvent,
      async () => [],
      undefined,
      async () => {
        throw new Error('egress_runtime_reconfigure_busy')
      },
    )

    await expect(runtime.setEgressMode('off')).rejects.toThrow(
      'egress_mode_change_blocked_by_active_runs',
    )
    expect(setEgressMode.mock.calls.map(([nextMode]) => nextMode)).toEqual([
      'off',
      'auto',
    ])
    expect(mode).toBe('auto')
    expect(restartService).not.toHaveBeenCalled()
    expect(emitEvent).toHaveBeenLastCalledWith(
      'egress-status-changed',
      expect.objectContaining({ mode: 'auto' }),
    )
  })
})
