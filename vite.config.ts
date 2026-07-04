import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // GitHub Pages project sites are served from https://<user>.github.io/<repo>/,
  // so all built asset paths must be prefixed with the repo name.
  base: '/promptlab/',
  plugins: [react()],
})
