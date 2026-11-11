import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 面板由插件自己的 Web 服务（默认 47613）托管，接口同源。
// 开发时把 /api 与 /fish_house 代理到本机插件服务。
const SERVER_TARGET = process.env.FISH_HOUSE_TARGET || "http://127.0.0.1:47613";

export default defineConfig({
  plugins: [vue()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": { target: SERVER_TARGET, changeOrigin: false },
      "/fish_house": { target: SERVER_TARGET, changeOrigin: false },
      "/ws": { target: SERVER_TARGET, ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: "../web/panel",
    emptyOutDir: true,
  },
});
