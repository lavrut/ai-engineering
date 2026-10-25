import { defineConfig } from 'vitest/config';

// Default environment is node; DOM tests opt in per file with
// `// @vitest-environment jsdom`.
export default defineConfig({
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    environment: 'node',
  },
});
