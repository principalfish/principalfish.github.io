import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['electionmapslogic/tests/**/*.test.js', 'referdle-solver/benchmarks/tests/**/*.test.mjs'],
    passWithNoTests: true,
  },
});
