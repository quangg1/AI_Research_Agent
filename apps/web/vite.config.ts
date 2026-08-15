import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = path.dirname(fileURLToPath(import.meta.url));
const contracts = path.resolve(root, "../../packages/contracts/src");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@kiln/contracts": path.join(contracts, "index.ts"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    fs: { allow: [root, contracts] },
    proxy: {
      "/v1": "http://localhost:3000",
      "/health": "http://localhost:3000",
    },
  },
});
