import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
export default defineConfig({
  clearScreen: false,
  plugins: [tailwindcss()],
  resolve: {alias: {"@": path.resolve(import.meta.dirname,"workbench/src")}},
  define: {__TERAX_BENCH__: "false"},
  build: { target: "es2022", rollupOptions:{input:{main:"index.html",workbench:"workbench.html",settings:"workbench-settings.html"}} },
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
    fs: { allow: [".", "../../assets", "../../local_mode/locales"] },
  },
});
