import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// ---------- Vite + Vitest 配置：开发与单元测试共用同一插件链 ----------
export default defineConfig({
  plugins: [vue()],
  // ---------- Vitest：纯工具函数使用 node 环境，避免拉起浏览器 DOM ----------
  test: {
    environment: 'node',
    include: ['src/**/*.{test,spec}.js']
  },
  server: {
    port: 5173,
    // 开发环境代理，将 /api 请求转发到 FastAPI 后端
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // ---------- 与前端 analyze 长超时一致，避免 dev 代理先于后端返回断开 ----------
        timeout: 600000,
        proxyTimeout: 600000
      }
    }
  }
})
