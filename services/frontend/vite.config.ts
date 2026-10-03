import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: '127.0.0.1', port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/auth': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/oauth': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/.well-known': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/auth.md': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/mcp-apps': { target: process.env.VITE_MCP_PROXY_TARGET || 'http://localhost:3000',  },
      '/mcp-direct': { target: process.env.VITE_MCP_PROXY_TARGET || 'http://localhost:3000',  },
      '/mcp': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001' },
      '/ws': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8001', ws: true },
    },
  },
});
