import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Windows 바인드 마운트는 inotify 이벤트가 컨테이너로 전달되지 않아 폴링이 필요합니다
    watch: { usePolling: true, interval: 300 },
  },
})
