export const PLATFORM_OPTIONS = ['desktop', 'webui', 'cli'] as const

export type Platform = (typeof PLATFORM_OPTIONS)[number]
