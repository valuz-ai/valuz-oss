import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from './navigation'

describe('navigation constants', () => {
  it('should expose the primary navigation shell entries', () => {
    expect(NAV_ITEMS.map((item) => item.path)).toEqual([
      '/',
      '/projects',
      '/knowledge',
      '/skills',
      '/settings',
    ])
  })
})
