import { WebPlatformProvider } from '@valuz/app/platform'
import { AppRouter } from './app/router'

export const App = () => (
  <WebPlatformProvider>
    <AppRouter />
  </WebPlatformProvider>
)
