// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import preact from '@astrojs/preact';

export default defineConfig({
  output: 'static',
  integrations: [mdx(), preact()],
  build: { inlineStylesheets: 'always', assets: 'assets' },
  vite: {
    // Lessons quote the code in ../harness, ../llms, ../ml, ../mcp, ../agents and ../data as raw text, and
    // the aids read ../llms, ../ml, ../mcp, ../agents and ../data data (only these folders, not the whole repo).
    server: { fs: { allow: ['.', '../harness', '../llms', '../ml', '../mcp', '../agents', '../data'] } },
    // The page host serves chunks from a nested path, so URLs built inside
    // JS chunks (e.g. Vite's preload helper) resolve relative to the chunk.
    experimental: {
      renderBuiltUrl: (_filename, { hostType }) => (hostType === 'js' ? { relative: true } : undefined),
    },
  },
});
