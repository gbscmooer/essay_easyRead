import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import type { ServerResponse } from 'http'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时代理 API 请求到后端
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        // 修复 SSE 缓冲：禁用代理响应的压缩和缓冲
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes: ServerResponse) => {
            // 如果是 SSE 流，移除可能导致缓冲的 header
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
})
