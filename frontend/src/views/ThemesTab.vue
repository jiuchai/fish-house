<template>
  <section class="tab-page">
    <div class="page-head">
      <div><h2>主题</h2><p class="muted">本地主题库；编辑会进入项目页，应用会覆盖当前壁纸配置。</p></div>
      <div class="head-actions">
        <label class="btn">导入主题<input type="file" accept=".fishtheme,.zip" hidden @change="onThemeImport" /></label>
        <button class="btn" type="button" @click="panel.tab = 'project'; createProject(false)">新建主题</button>
      </div>
    </div>
    <div class="theme-grid" v-if="panel.overview.themes.length">
      <ThemeCard v-for="item in panel.overview.themes" :key="item.id" :item="item"
        :active="item.id === panel.overview.active_theme_id"
        @apply="applyTheme" @remove="deleteTheme" @edit="openThemeProject"></ThemeCard>
    </div>
    <div v-else class="empty">还没有主题，点「新建主题」或从市场下载。</div>
  </section>
</template>

<script setup>
import ThemeCard from "../components/ThemeCard.vue";
import { panel, onThemeImport, applyTheme, deleteTheme, openThemeProject, createProject } from "../store.js";
</script>
