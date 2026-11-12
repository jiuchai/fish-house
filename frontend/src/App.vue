<template>
  <header class="app-header">
    <div class="brand">
      <div class="brand-mark">屋</div>
      <div>
        <h1>大肥鱼的小屋</h1>
      </div>
    </div>
    <div class="header-right">
      <span class="status-pill" :class="'mode-' + (panel.live.mode || 'rest')">
        <i></i>{{ modeLabel(panel.live.mode) }}
      </span>
      <button v-if="!panel.market.token" class="btn small" type="button" @click="openAuth('login')">登录</button>
      <span v-else class="user-pill" @click="panel.tab = 'my'">{{ panel.market.user }}</span>
      <button class="icon-btn" type="button" title="刷新" @click="load" :disabled="panel.loading">⟳</button>
      <span class="version">v{{ panel.overview?.version || '—' }}</span>
    </div>
  </header>

  <nav class="tabs" aria-label="面板导航">
    <button v-for="item in TABS" :key="item.key" type="button"
      class="tab" :class="{ active: panel.tab === item.key }" @click="panel.tab = item.key">
      <span v-html="item.icon"></span>{{ item.label }}
    </button>
  </nav>

  <div v-if="panel.error" class="banner error">
    <b>接口请求失败：</b>{{ panel.error }}
    <button class="btn ghost small" type="button" @click="load">重试</button>
  </div>

  <main v-if="panel.overview" ref="contentEl" class="content">
    <OverviewTab v-show="panel.tab === 'overview'" />
    <ThemesTab v-show="panel.tab === 'themes'" />
    <MarketTab v-show="panel.tab === 'market'" />
    <ProjectTab v-show="panel.tab === 'project'" />
    <MyTab v-show="panel.tab === 'my'" />
    <SettingsTab v-show="panel.tab === 'settings'" />
  </main>

  <div v-else-if="panel.loading" class="loading"><span class="spinner"></span>正在读取插件数据…</div>

  <footer class="footer">© 2026 大肥鱼的小屋 · jiuchai · v{{ panel.overview?.version || '' }}</footer>

  <div v-if="panel.toastData.show" class="toast show" :class="panel.toastData.type">{{ panel.toastData.text }}</div>

  <PreviewDialog />
  <ConfirmDialog />
  <AuthDialog />
  <UploadDialog />
  <MarketEditDialog />
  <ConflictDialog />
  <DownloadDialog />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { TABS } from "./constants.js";
import {
  panel, load, connectLive, closeLive, modeLabel, marketSearch, loadMy, openAuth,
} from "./store.js";
import OverviewTab from "./views/OverviewTab.vue";
import ThemesTab from "./views/ThemesTab.vue";
import MarketTab from "./views/MarketTab.vue";
import ProjectTab from "./views/ProjectTab.vue";
import MyTab from "./views/MyTab.vue";
import SettingsTab from "./views/SettingsTab.vue";
import PreviewDialog from "./components/dialogs/PreviewDialog.vue";
import ConfirmDialog from "./components/dialogs/ConfirmDialog.vue";
import AuthDialog from "./components/dialogs/AuthDialog.vue";
import UploadDialog from "./components/dialogs/UploadDialog.vue";
import MarketEditDialog from "./components/dialogs/MarketEditDialog.vue";
import ConflictDialog from "./components/dialogs/ConflictDialog.vue";
import DownloadDialog from "./components/dialogs/DownloadDialog.vue";

const contentEl = ref(null);

function onShellWheel(event) {
  if (event.target?.closest?.(".canvas, .modal, .el-popper, .el-select-dropdown, video")) return;
  if (!contentEl.value || !event.deltaY) return;
  contentEl.value.scrollTop += event.deltaY;
  event.preventDefault();
}

watch(() => panel.tab, (value) => {
  if (value === "market" && !panel.market.loaded) marketSearch();
  if (value === "my") loadMy();
});

onMounted(async () => {
  await load();
  connectLive();
  window.addEventListener("wheel", onShellWheel, { passive: false });
});

onBeforeUnmount(() => {
  closeLive();
  window.removeEventListener("wheel", onShellWheel);
});
</script>
