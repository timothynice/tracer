import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        'src/test/',
        '**/*.{config,test}.{js,ts}',
        '**/dist/**'
      ]
    },
    // Mock CSS imports
    css: false
  },
  resolve: {
    alias: {
      '@': '/src'
    }
  }
})