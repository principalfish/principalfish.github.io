import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['electionmaps/tests/**/*.test.js'],
    passWithNoTests: true,
  },
});
