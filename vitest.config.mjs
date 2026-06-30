import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['electionmapslogic/tests/**/*.test.js'],
    passWithNoTests: true,
  },
});
