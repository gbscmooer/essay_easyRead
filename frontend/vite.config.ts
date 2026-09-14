import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'
import type { IncomingMessage } from 'http'

function manualChunks(id: string): string | undefined {
  const normalized = id.replace(/\\/g, '/')
  if (!normalized.includes('/node_modules/')) return undefined

  const groups: Array<[string, string[]]> = [
    ['vendor-pdf', ['react-pdf', 'pdfjs-dist']],
    ['vendor-mermaid', ['mermaid']],
    ['vendor-charts', ['recharts']],
    ['vendor-graph', ['react-force-graph-2d']],
    ['vendor-hljs', ['highlight.js']],
    ['vendor-react', ['react', 'react-dom', 'react-router', 'react-router-dom']],
  ]

  for (const [chunk, packages] of groups) {
    if (packages.some(pkg => normalized.includes(`/node_modules/${pkg}/`))) return chunk
  }
  return undefined
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes: IncomingMessage) => {
            const contentType = proxyRes.headers?.['content-type'] || ''
            if (contentType.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
              delete proxyRes.headers['content-encoding']
            }
          })
        },
      },
    },
  },
  build: {
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
})
