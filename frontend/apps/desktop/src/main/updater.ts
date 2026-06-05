import { app, type BrowserWindow } from 'electron'
import updaterModule from 'electron-updater'

const { autoUpdater } = updaterModule

interface SetupUpdaterOptions {
  getMainWindow: () => BrowserWindow | null
}

export const setupUpdater = ({ getMainWindow }: SetupUpdaterOptions) => {
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  const customFeedUrl = process.env.VALUZ_UPDATER_URL
  if (customFeedUrl) {
    autoUpdater.setFeedURL({
      provider: 'generic',
      url: customFeedUrl,
    })
  }

  const sendToRenderer = (event: string, payload?: unknown) => {
    const mainWindow = getMainWindow()
    if (!mainWindow) {
      return
    }

    mainWindow.webContents.send(event, payload)
  }

  autoUpdater.on('checking-for-update', () => {
    sendToRenderer('updater:checking')
  })

  autoUpdater.on('update-available', (info) => {
    sendToRenderer('updater:available', info)
  })

  autoUpdater.on('update-not-available', (info) => {
    sendToRenderer('updater:not-available', info)
  })

  autoUpdater.on('download-progress', (progress) => {
    sendToRenderer('updater:progress', progress)
  })

  autoUpdater.on('update-downloaded', (info) => {
    sendToRenderer('updater:downloaded', info)
  })

  autoUpdater.on('error', (error) => {
    sendToRenderer('updater:error', { message: error.message })
  })

  const checkForUpdates = async () => {
    if (process.env.NODE_ENV === 'development') {
      sendToRenderer('updater:not-available', { reason: 'development-mode' })
      return
    }

    await autoUpdater.checkForUpdates()
  }

  const quitAndInstall = () => {
    autoUpdater.quitAndInstall()
  }

  return {
    checkForUpdates,
    quitAndInstall,
  }
}

export const scheduleUpdateCheck = async (checkForUpdates: () => Promise<void>) => {
  if (!app.isPackaged) {
    return
  }

  await checkForUpdates()
  setInterval(() => {
    void checkForUpdates()
  }, 30 * 60 * 1000)
}
