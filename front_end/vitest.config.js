import { fileURLToPath } from 'node:url'
export default {
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.spec.js'],
    coverage: {
      reporter: ['text', 'html'],
    },
  },
}
