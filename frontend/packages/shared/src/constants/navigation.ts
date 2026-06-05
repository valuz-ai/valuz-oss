export interface NavigationItem {
  path: string
  label: string
  description: string
}

export const NAV_ITEMS: NavigationItem[] = [
  {
    path: '/',
    label: 'Conversations',
    description: 'Recent chats, drafts, and local session entrypoints.',
  },
  {
    path: '/projects',
    label: 'Projects',
    description: 'Project lists with files, notes, and scoped agent context.',
  },
  {
    path: '/knowledge',
    label: 'Knowledge',
    description: 'Imported documents and local knowledge collections.',
  },
  {
    path: '/skills',
    label: 'Skills',
    description: 'Official and custom skills available to the local agent.',
  },
  {
    path: '/settings',
    label: 'Settings',
    description: 'Models, credentials, and local desktop preferences.',
  },
]
