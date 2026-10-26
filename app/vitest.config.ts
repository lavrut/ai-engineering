import { defineConfig } from 'vitest/config';

// Default environment is node; DOM tests opt in per file with
// `// @vitest-environment jsdom`.
export default defineConfig({
  // As in astro.config.mjs: allow reading ../llms and ../ml (the data the aids
  // read), which jsdom tests otherwise refuse (the web environment applies
  // Vite's fs.allow check).
  server: { fs: { allow: ['.', '../llms', '../ml'] } },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    environment: 'node',
  },
});
