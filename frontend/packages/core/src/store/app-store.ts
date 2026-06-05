import { create } from 'zustand'
import { NAV_ITEMS, type NavigationItem } from '@valuz/shared'
import { activeProfile } from '../edition'
import type { Edition, FeatureFlags } from '../edition/profile'

interface AppStoreState {
  edition: Edition
  features: FeatureFlags
  navItems: NavigationItem[]
}

export const useAppStore = create<AppStoreState>(() => ({
  edition: activeProfile.edition,
  features: activeProfile.features,
  navItems: NAV_ITEMS,
}))
