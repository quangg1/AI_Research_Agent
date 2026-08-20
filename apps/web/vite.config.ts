import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = path.dirname(fileURLToPath(import.meta.url));
const contracts = path.resolve(root, "../../packages/contracts/src");
const zodEntry = path.resolve(root, "node_modules/zod");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@kiln/contracts": path.join(contracts, "index.ts"),
      zod: zodEntry,
    },
    dedupe: ["zod"],
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
