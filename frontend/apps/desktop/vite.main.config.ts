import { builtinModules } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'

const nodeBuiltins = new Set([
  ...builtinModules,
  ...builtinModules.map((name) => `node:${name}`),
])

export default defineConfig({
  resolve: {
    alias: {
      '@valuz/shared': path.resolve(__dirname, '../../packages/shared/src'),
      '@valuz/core': path.resolve(__dirname, '../../packages/core/src'),
      '@valuz/ui': path.resolve(__dirname, '../../packages/ui/src'),
    },
  },
  build: {
    outDir: 'dist-electron',
    emptyOutDir: false,
    sourcemap: true,
    lib: {
      entry: path.resolve(__dirname, 'src/main/index.ts'),
      formats: ['es'],
      fileName: () => 'main.js',
    },
    rollupOptions: {
      external: (id) => id === 'electron' || id === 'electron-updater' || nodeBuiltins.has(id),
    },
  },
})
