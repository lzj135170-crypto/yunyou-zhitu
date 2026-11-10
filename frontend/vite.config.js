import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 开发时前端跑在 5173，接口和瓦片代理到 FastAPI 的 8000。
    // 8000 被其他项目占用时可用 TRAVEL_API_TARGET 指向备用端口。
    proxy: {
      '/api': {
        target: process.env.TRAVEL_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
