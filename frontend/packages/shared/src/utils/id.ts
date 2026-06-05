export const createId = (prefix: string) => `${prefix}-${Math.random().toString(36).slice(2, 10)}`
