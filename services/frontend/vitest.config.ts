import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    globals: true,
    css: true,
    exclude: [
      "**/node_modules/**",
      "tests/e2e/**",
      "**/*.spec.ts",
      "scripts/**",
      "src/legacy/**",
    ],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html", "lcov"],
      exclude: [
        "node_modules/",
        "src/test/",
        "**/*.d.ts",
        "**/*.test.*",
        "**/*.spec.*",
        "**/*.config.*",
        "**/dist/**",
        "**/mocks/**",
        "src/main.tsx", // App entry point
        "src/vite-env.d.ts",
      ],
      all: true,
      clean: true,
      thresholds: {
        global: {
          branches: 80,
          functions: 80,
          lines: 80,
          statements: 80,
        },
      },
    },
    // Mock Vite's import.meta.env
    define: {
      "import.meta.env.NODE_ENV": '"test"',
      "import.meta.env.VITE_API_URL": '"http://localhost:8001"',
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
