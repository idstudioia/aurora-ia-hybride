import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const NO_FRAMING = { 'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'" };

export default defineConfig({
  plugins: [react()],
  // No other page may frame the interface (clickjacking of an « Autoriser » button).
  server: { host: '127.0.0.1', port: 5173, strictPort: true, headers: NO_FRAMING },
  preview: { host: '127.0.0.1', headers: NO_FRAMING },
  // The 3D file readers are imported on demand only (labo/maillage.ts, a model the user gives; labo/origine.ts, its
  // « vue d'origine », 12/11/2026). Prepared at start: found at the first use instead, the dev server reloads the page
  // and the scene being opened is lost.
  optimizeDeps: {
    include: ['three/examples/jsm/loaders/STLLoader.js', 'three/examples/jsm/loaders/OBJLoader.js',
      'three/examples/jsm/loaders/GLTFLoader.js', 'three/examples/jsm/environments/RoomEnvironment.js'],
  },
  build: {
    // land-50m (≈ 546 kB, Natural Earth topology) is loaded on demand by the 3D world only.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // Rollup names a shared chunk after its first module (« SkyIcon »): the globe / d3-geo code
        // shared by the home and world pages gets a readable name instead.
        chunkFileNames: chunk => (!chunk.isDynamicEntry && chunk.moduleIds.some(id => /[\\/]node_modules[\\/]d3-geo[\\/]/.test(id))
          ? 'assets/globe-d3-[hash].js' : 'assets/[name]-[hash].js'),
      },
    },
  },
});
