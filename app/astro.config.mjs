// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import preact from '@astrojs/preact';

export default defineConfig({
  output: 'static',
  integrations: [mdx(), preact()],
  build: { inlineStylesheets: 'always', assets: 'assets' },
  vite: {
    // Lessons import the reference harness (../harness) as raw text.
    server: { fs: { allow: ['..'] } },
    // The page host serves chunks from a nested path, so URLs built inside
    // JS chunks (e.g. Vite's preload helper) resolve relative to the chunk.
    experimental: {
      renderBuiltUrl: (_filename, { hostType }) => (hostType === 'js' ? { relative: true } : undefined),
    },
  },
});
