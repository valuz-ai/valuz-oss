import { test as base, type ElectronApplication, type Page } from '@playwright/test'
import { _electron } from '@playwright/test'
import * as path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

type ElectronFixtures = {
  electronApp: ElectronApplication
  window: Page
}

export const test = base.extend<ElectronFixtures>({
  electronApp: async ({}, use) => {
    const appPath = path.resolve(__dirname, '../../apps/desktop')
    const app = await _electron.launch({
      args: [path.join(appPath, 'dist-electron/main.js')],
      cwd: appPath,
      env: {
        ...process.env,
        VITE_DEV_SERVER_URL: 'http://localhost:1420',
      },
    })
    await use(app)
    await app.close()
  },
  window: async ({ electronApp }, use) => {
    const page = await electronApp.firstWindow()
    await page.waitForLoadState('domcontentloaded')
    await use(page)
  },
})

export { expect } from '@playwright/test'
