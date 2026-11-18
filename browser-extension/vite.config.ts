import { resolve } from 'node:path';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { buildManifest, type Target } from './src/manifest';
import pkg from './package.json';

// Builds the extension into dist/chrome (default) or dist/firefox (TARGET=firefox).
// The side panel reuses the web app's React code from ../frontend/src (graph, panels, methodology), so the extension
// always shows the same result view as sourcecheck.tech. VITE_API_BASE overrides the default backend at build time.
const target = (process.env.TARGET === 'firefox' ? 'firefox' : 'chrome') as Target;

const manifest = (): Plugin => ({
  name: 'source-check-manifest',
  generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'manifest.json', source: JSON.stringify(buildManifest(target, pkg.version, process.env.E2E === '1'), null, 2) });
  },
});

export default defineConfig({
  base: '',
  plugins: [react(), manifest()],
  resolve: {
    // the shared frontend files import react too: always this package's copy (frontend/node_modules may not exist)
    alias: {
      '@app': resolve(__dirname, '../frontend/src'),
      react: resolve(__dirname, 'node_modules/react'),
      'react-dom': resolve(__dirname, 'node_modules/react-dom'),
    },
    dedupe: ['react', 'react-dom'],          // one React for our code and the shared frontend components
  },
  server: { fs: { allow: ['..'] } },
  build: {
    outDir: process.env.E2E === '1' ? 'dist/e2e' : `dist/${target}`,
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    modulePreload: { polyfill: false },     // every target browser has modulepreload; the polyfill touches `document`
    rollupOptions: {
      input: {
        sidepanel: resolve(__dirname, 'sidepanel.html'),
        options: resolve(__dirname, 'options.html'),
        background: resolve(__dirname, 'src/background.ts'),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === 'background' ? 'background.js' : 'assets/[name]-[hash].js'),
      },
    },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
