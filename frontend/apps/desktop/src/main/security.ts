import { shell } from 'electron'

export const isSafeExternalUrl = (value: string) => /^https?:\/\//.test(value)

export const openExternalIfSafe = async (value: string) => {
  if (!isSafeExternalUrl(value)) {
    return false
  }

  await shell.openExternal(value)
  return true
}
