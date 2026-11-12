<template>
  <section class="tab-page">
    <div class="page-head"><div><h2>我的</h2><p class="muted">发布过的、收藏和点赞的主题。</p></div></div>
    <section v-if="!panel.market.token" class="card login-cta">
      <div><b>还没有登录</b><p class="muted">登录后可以查看你发布的主题、收藏和点赞。</p></div>
      <button class="btn primary" type="button" @click="openAuth('login')">登录 / 注册</button>
    </section>
    <template v-else>
      <section class="card profile-card">
        <div><b>{{ panel.market.user }}</b><p class="muted">已登录主题市场</p></div>
        <button class="btn ghost small" type="button" @click="logout">退出</button>
      </section>
      <div class="my-tabs">
        <button type="button" :class="{ active: panel.myKind === 'published' }" @click="selectKind('published')">我发布的</button>
        <button type="button" :class="{ active: panel.myKind === 'favorites' }" @click="selectKind('favorites')">收藏</button>
        <button type="button" :class="{ active: panel.myKind === 'likes' }" @click="selectKind('likes')">点赞</button>
      </div>
      <div class="theme-grid" v-if="panel.myItems.length">
        <ThemeCard v-for="item in panel.myItems" :key="item.id" :item="item" market
          :show-actions="panel.myKind === 'published'" @apply="marketDownload" @remove="marketDelete"
          @like="toggleLike" @favorite="toggleFavorite" @edit="openMarketEdit" @update="openUploadDialog(item.id)"></ThemeCard>
      </div>
      <div v-else class="empty">这里还没有内容。</div>
    </template>
  </section>
</template>

<script setup>
import ThemeCard from "../components/ThemeCard.vue";
import {
  panel, openAuth, logout, loadMy, marketDownload, marketDelete, toggleLike, toggleFavorite,
  openMarketEdit, openUploadDialog,
} from "../store.js";

function selectKind(kind) {
  panel.myKind = kind;
  loadMy();
}
</script>
