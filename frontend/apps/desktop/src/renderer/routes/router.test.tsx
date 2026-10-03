import { render, screen } from '@testing-library/react'
import { Outlet, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeAll, describe, expect, it } from 'vitest'
import { initI18n } from '@valuz/shared/i18n'
import { createPluginHost, personalProfile, useRegistryStore } from '@valuz/core'
import { loadOssPlugins } from '@valuz/app/plugins'
import {
  createAppRouteObjects,
  getResolvedDesktopRoutes,
  type ResolvedRoute,
} from './route-registry'

let resolvedDesktopRoutes: ResolvedRoute[] = []

beforeAll(async () => {
  initI18n({ locale: 'zh-CN', fallbackLocale: 'zh-CN' })
  // The routes are registered by the OSS plugins.
  useRegistryStore.getState().clearLayers()
  useRegistryStore.getState().hydrate(personalProfile)
  await loadOssPlugins(createPluginHost(), { inactive: [] })
  resolvedDesktopRoutes = getResolvedDesktopRoutes()
})

const TestProjectLayout = () => (
  <div>
    <nav aria-label="Prototype desktop sidebar">
      <button type="button" aria-label="Valuz Agent menu">
        Valuz
      </button>
      <a href="/conversation/new">新对话</a>
      <a href="/projects">项目</a>
      <a href="/knowledge">知识库</a>
    </nav>
    <Outlet />
  </div>
)

function renderDesktopRoute(initialEntry: string) {
  const router = createMemoryRouter(
    createAppRouteObjects({
      routes: resolvedDesktopRoutes,
      Root: Outlet,
      layout: TestProjectLayout,
      routeOverrides: {
        'conversation-detail': () => <div>conversation route mounted</div>,
        knowledge: () => <div>knowledge route mounted</div>,
        onboarding: () => <div>索引中</div>,
      },
    }),
    {
      initialEntries: [initialEntry],
    },
  )

  render(<RouterProvider router={router} />)
}

describe('desktop routes', () => {
  it('registers prototype parity routes as hidden project routes', () => {
    const hiddenPrototypeRoutes = ['tool-calls', 'context-panel', 'overlays', 'automation']

    for (const routeId of hiddenPrototypeRoutes) {
      const route = resolvedDesktopRoutes.find((candidate) => candidate.id === routeId)
      expect(route).toBeDefined()
      expect(route?.layout).toBe('project')
      expect(route?.showInNav).toBe(false)
    }
  })

  it('renders the personal conversation project route', async () => {
    renderDesktopRoute('/conversation/local-agent')

    expect(await screen.findByText('conversation route mounted')).toBeTruthy()
  })

  it('renders the fe-style desktop sidebar chrome around project routes', async () => {
    renderDesktopRoute('/knowledge')

    expect(await screen.findByLabelText('Valuz Agent menu')).toBeTruthy()
    expect(screen.getByText('knowledge route mounted')).toBeTruthy()
    expect(screen.getByText('新对话')).toBeTruthy()
    expect(screen.getByText('项目')).toBeTruthy()
    expect(screen.getByText('知识库')).toBeTruthy()
  })

  it('renders the onboarding page', async () => {
    renderDesktopRoute('/onboarding')

    expect(await screen.findByText('索引中')).toBeTruthy()
  })
})
