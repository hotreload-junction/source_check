import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev` (port 5173) talks to the real backend via /api.
// `npm run dev:mock` (port 5174, --mode mock) serves contracts/examples + src/mocks, no backend needed.
// Override the backend address with API_TARGET=http://host:port.
export default defineConfig({
  plugins: [react()],
  server: {
    strictPort: true,
    fs: { allow: ['..'] },
    proxy: { '/api': { target: process.env.API_TARGET || 'http://localhost:8000', changeOrigin: true } },
  },
});
