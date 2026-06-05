export interface UserProfile {
  id: string
  displayName: string
  email?: string
  role: 'owner' | 'member' | 'viewer'
}
