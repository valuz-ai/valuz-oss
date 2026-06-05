import { create } from 'zustand'
import type { UserProfile } from '@valuz/shared'

interface AuthStoreState {
  currentUser: UserProfile | null
  authenticated: boolean
  setCurrentUser: (user: UserProfile | null) => void
}

export const useAuthStore = create<AuthStoreState>((set) => ({
  currentUser: null,
  authenticated: false,
  setCurrentUser: (currentUser) => set({ currentUser, authenticated: Boolean(currentUser) }),
}))
