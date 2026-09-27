import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 裸进程开发：/api /sub 代理到本机 API（§10）；compose 使用 web 同源反代
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8443', changeOrigin: false },
      '/sub': { target: 'http://127.0.0.1:8443', changeOrigin: false },
    },
  },
})
