import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

declare const process: { env: Record<string, string | undefined> }

export default defineConfig(() => {
  const backendUrl = process.env.SALESMIND_DEV_BACKEND_URL ?? 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': backendUrl,
        '/health': backendUrl,
      },
    },
  }
})
