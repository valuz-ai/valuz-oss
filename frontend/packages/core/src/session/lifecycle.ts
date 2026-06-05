export const SESSION_PHASES = ['idle', 'booting', 'active', 'stopped'] as const

export type SessionPhase = (typeof SESSION_PHASES)[number]
