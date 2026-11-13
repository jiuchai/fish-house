import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 面板由插件自己的服务（默认 47613/47614）托管，构建产物放进 web/panel；
// pages/panel 是 AstrBot 插件页的跳板，只负责把浏览器带到控制台。
// 开发时把 /api 与 /fish_house 代理到 AstrBot 控制台（默认 6185）。
const SERVER_TARGET = process.env.FISH_HOUSE_TARGET || "http://127.0.0.1:6185";

export default defineConfig({
  plugins: [vue()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": { target: SERVER_TARGET, changeOrigin: false },
      "/fish_house": { target: SERVER_TARGET, changeOrigin: false },
    },
  },
  build: {
    outDir: "../web/panel",
    emptyOutDir: true,
  },
});
