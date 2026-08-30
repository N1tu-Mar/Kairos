import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // `server-only` throws on import outside a Server Component — the guard
      // that keeps the bearer token out of a client bundle, and also what
      // stops a test importing the route handlers that need checking.
      //
      // Resolved to the package's own `empty.js`, which is exactly what its
      // `react-server` export condition points at: the same no-op Next.js
      // gets when it loads this module on the server. Not a stub of ours —
      // the test sees what production sees, and the guard is untouched for
      // the client bundle, which is the thing it exists to protect.
      "server-only": fileURLToPath(
        new URL("./node_modules/server-only/empty.js", import.meta.url),
      ),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
