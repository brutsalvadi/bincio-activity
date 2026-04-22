import { defineConfig } from "astro/config";
import { loadEnv } from "vite";
import svelte from "@astrojs/svelte";
import tailwind from "@astrojs/tailwind";

const env = loadEnv(process.env.NODE_ENV ?? 'development', process.cwd(), '');
// PUBLIC_EDIT_URL: non-empty → bincio edit URL; empty → proxy to bincio serve.
// VITE_API_PORT lets `bincio dev` override the serve port without touching .env.
const apiPort = process.env.VITE_API_PORT || '4041';
const serveTarget = env.PUBLIC_EDIT_URL || `http://localhost:${apiPort}`;

export default defineConfig({
  integrations: [svelte(), tailwind()],
  devToolbar: { enabled: false },
  output: "static",
  // When hosting at a subdirectory (e.g. GitHub Pages project site), set:
  // base: "/repo-name",
  vite: {
    optimizeDeps: {
      include: ['maplibre-gl'],
      esbuildOptions: { target: 'es2022' },
    },
    build: { target: 'es2022' },
    // Proxy /api/* to bincio serve/edit so cookies work same-origin in dev.
    // In production nginx handles this — same pattern, no code change needed.
    server: {
      proxy: {
        // Both /api/upload and /api/upload/strava-zip return SSE streams in response
        // to POST requests. Vite's default proxy buffers the full body before forwarding,
        // which breaks streaming and causes EPIPE on long uploads.
        // selfHandleResponse + manual pipe sends chunks as they arrive.
        '/api/upload': {
          target: serveTarget,
          changeOrigin: true,
          selfHandleResponse: true,
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes, req, res) => {
              res.writeHead(proxyRes.statusCode ?? 200, proxyRes.headers);
              proxyRes.pipe(res, { end: true });
            });
            proxy.on('error', (err, _req, res) => {
              if (err.code === 'EPIPE' || err.code === 'ECONNRESET') return;
              if (!res.headersSent) {
                res.writeHead(502);
                res.end('proxy error');
              }
            });
          },
        },
        '/api': {
          target: serveTarget,
          changeOrigin: true,
        },
      },
    },
  },
});
