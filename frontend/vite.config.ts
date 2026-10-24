import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// API and private media remain behind the backend's session authorization.
// Never add the server-side .local/storage directory to publicDir or fs.allow.
export default defineConfig(({ mode }) => ({
  plugins: [react(), {
    name: 'public-demo-document',
    transformIndexHtml: (html) => mode === 'demo' ? html
      .replace('<title>Touchline · Evidence before opinion</title>', '<title>Scoutroom Demo · Evidence before opinion</title>')
      .replace('Touchline — a private, evidence-led youth soccer scouting workspace.', 'Explore Scoutroom with fictional player profiles, match evidence and transparent comparisons. Read-only demo; no real youth data.') : html,
  }],
  publicDir: mode === 'demo' ? 'demo-public' : false,
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['localhost', '.devinapps.com', '.devin.ai'],
    fs: {
      strict: true,
      allow: [fileURLToPath(new URL('.', import.meta.url))],
      deny: ['.env', '.env.*', '*.{crt,pem,key}', '**/.git/**', '**/.local/**', '**/backend/**'],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
}));
