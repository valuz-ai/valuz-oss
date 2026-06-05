import { render, screen } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { routes } from './router'
import { resolvedDesktopRoutes } from './route-registry'

describe('desktop routes', () => {
  it('registers prototype parity routes as hidden workspace routes', () => {
    const hiddenPrototypeRoutes = ['tool-calls', 'context-panel', 'overlays', 'scheduled']

    for (const routeId of hiddenPrototypeRoutes) {
      const route = resolvedDesktopRoutes.find((candidate) => candidate.id === routeId)
      expect(route).toBeDefined()
      expect(route?.layout).toBe('workspace')
      expect(route?.showInNav).toBe(false)
    }
  })

  it('renders the personal conversation workspace route', async () => {
    const router = createMemoryRouter(routes, {
      initialEntries: ['/conversation/local-agent'],
    })

    render(<RouterProvider router={router} />)

    expect((await screen.findAllByText('英伟达 Q4 财报分析')).length).toBeGreaterThan(0)
  })

  it('renders the fe-style desktop sidebar chrome around workspace routes', async () => {
    const router = createMemoryRouter(routes, {
      initialEntries: ['/knowledge'],
    })

    render(<RouterProvider router={router} />)

    expect(await screen.findByText('快速对话')).toBeTruthy()
    expect(screen.getAllByText('Projects').length).toBeGreaterThan(0)
    expect(screen.getByText('Recents')).toBeTruthy()
    expect(screen.getByText('知识库')).toBeTruthy()
  })

  it('renders the onboarding page', async () => {
    const router = createMemoryRouter(routes, {
      initialEntries: ['/onboarding'],
    })

    render(<RouterProvider router={router} />)

    expect(await screen.findByText('文件解析')).toBeTruthy()
  })
})
