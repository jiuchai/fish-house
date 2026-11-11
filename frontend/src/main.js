import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./style.css";
import App from "./App.vue";
import ThemeCard from "./components/ThemeCard.vue";
import CanvasPanel from "./components/CanvasPanel.vue";

const app = createApp(App);
app.use(ElementPlus);
app.component("theme-card", ThemeCard);
app.component("canvas-panel", CanvasPanel);
app.mount("#app");
