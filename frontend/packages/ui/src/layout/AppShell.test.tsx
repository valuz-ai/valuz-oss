import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@valuz/shared'
import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('should render the navigation, content, and context panel slots', () => {
    render(
      <AppShell
        appTitle="Valuz Agent"
        navItems={NAV_ITEMS}
        activePath="/chat"
        aside={<div>Context panel</div>}
      >
        <div>Primary content</div>
      </AppShell>
    )

    expect(screen.getByRole('navigation', { name: 'Project sections' })).toBeTruthy()
    expect(screen.getByText('Primary content')).toBeTruthy()
    expect(screen.getByText('Context panel')).toBeTruthy()
  })

  it('supports the prototype shell aliases and optional hidden header', () => {
    render(
      <AppShell title="Prototype page" right={<div>Right panel</div>} hideHeader>
        <div>Prototype content</div>
      </AppShell>
    )

    expect(screen.getByText('Prototype content')).toBeTruthy()
    expect(screen.getByText('Right panel')).toBeTruthy()
    expect(screen.queryByRole('banner')).toBeNull()
  })

  it('renders a custom sidebar when provided', () => {
    render(
      <AppShell sidebar={<div>Prototype sidebar</div>} shellClassName="bg-[#F8F9FB]">
        <div>Project body</div>
      </AppShell>
    )

    expect(screen.getByText('Prototype sidebar')).toBeTruthy()
    expect(screen.getByText('Project body')).toBeTruthy()
    expect(screen.queryByRole('navigation', { name: 'Project sections' })).toBeNull()
  })
})
