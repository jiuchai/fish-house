<template>
  <section class="tab-page">
    <div class="home-top">
      <div class="hero-card" :class="{ 'has-cover': activeTheme && activeTheme.cover_url }" :style="heroStyle"
        @click="activeTheme?.cover_url && (panel.coverPreview = activeTheme.cover_url)">
        <div class="hero-theme">
          <p class="eyebrow">CURRENT THEME</p>
          <h2>{{ activeTheme?.name || overview.theme_meta.name || '我的主题' }}</h2>
          <p class="hero-sub">{{ activeTheme?.description || overview.theme_meta.description || '还没有写主题介绍。' }}</p>
          <div class="hero-meta">
            <span>{{ activeTheme?.author || overview.theme_meta.author || '匿名作者' }}</span>
            <span>v{{ activeTheme?.version || overview.theme_meta.version || '1.0.0' }}</span>
            <span class="hero-status">当前：{{ modeLabel(panel.live.mode) }}</span>
          </div>
        </div>
        <div class="hero-stats">
          <div><b>{{ overview.library.length }}</b><span>素材</span></div>
          <div><b>{{ overview.flow.edges.length }}</b><span>连线</span></div>
          <div><b>{{ overview.flow.nodes.length }}</b><span>节点</span></div>
          <div><b>{{ overview.themes.length }}</b><span>主题</span></div>
        </div>
      </div>

      <section class="card">
        <div class="card-head"><h3>壁纸链接</h3><span class="tag" :class="overview.public_ready ? 'good' : 'warn'">{{ overview.public_ready ? '已同步' : '未同步' }}</span></div>
        <div class="input-row">
          <input :value="publicUrl" readonly />
          <button class="btn" type="button" @click="copy(publicUrl)">复制</button>
        </div>
        <label class="switch-line">显示底部气泡<el-switch v-model="panel.settingsForm.bubble_enabled" @change="toggleBubble" /></label>
      </section>
    </div>

    <section class="card home-flow">
      <div class="card-head"><h3>当前画布</h3><button class="btn ghost small" type="button" @click="panel.tab = 'project'">编辑项目</button></div>
      <CanvasPanel readonly :flow="overview.flow" :state="panel.live" :library="overview.library"
        :toast="toast" :confirm="confirm" />
    </section>
  </section>
</template>

<script setup>
import { computed } from "vue";
import CanvasPanel from "../components/CanvasPanel.vue";
import {
  panel, activeTheme, heroStyle, publicUrl, modeLabel, copy, toast, confirm, toggleBubble,
} from "../store.js";

// 面板只有在 overview 加载完成后才会渲染，这里直接取用即可。
const overview = computed(() => panel.overview);
</script>
