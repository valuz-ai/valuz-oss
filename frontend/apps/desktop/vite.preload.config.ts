import path from 'node:path'
import { defineConfig } from 'vite'

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
      entry: path.resolve(__dirname, 'src/preload/index.ts'),
      formats: ['cjs'],
      fileName: () => 'preload.js',
    },
    rollupOptions: {
      external: ['electron', /^node:/],
    },
  },
})
