export interface DesktopRuntimeEnv {
  appDataDir: string
}

export const buildDesktopRuntimeEnv = (appDataDir: string): DesktopRuntimeEnv => ({
  appDataDir,
})
