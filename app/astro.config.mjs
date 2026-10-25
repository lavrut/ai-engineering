// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import preact from '@astrojs/preact';

export default defineConfig({
  output: 'static',
  integrations: [mdx(), preact()],
  build: { inlineStylesheets: 'always' },
  vite: {
    // Lessons import the reference harness (../harness) as raw text.
    server: { fs: { allow: ['..'] } },
  },
});
