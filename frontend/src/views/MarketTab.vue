<template>
  <section class="tab-page market-page">
    <div class="page-head market-head">
      <div><h2>主题市场</h2><p class="muted">浏览、下载并应用别人的主题，或上传自己的配置；登录后可上传自己的主题。</p></div>
      <div class="market-head-search">
        <input v-model="panel.market.query" class="search" placeholder="搜索主题 / 作者" @keyup.enter="marketSearch()" />
        <button class="btn" type="button" @click="marketSearch()">搜索</button>
      </div>
      <div class="head-actions">
        <button class="btn" type="button" @click="marketSearch()" :disabled="panel.market.busy">刷新</button>
        <button class="btn primary" type="button" @click="openUploadDialog()" :disabled="panel.market.busy || !panel.market.token">上传 / 更新</button>
      </div>
    </div>
    <p v-if="panel.market.message" class="market-inline-msg">{{ panel.market.message }}</p>
    <div class="market-scroll">
      <div class="theme-grid" v-if="panel.market.items.length">
        <ThemeCard v-for="item in panel.market.items" :key="item.id" :item="item" market
          @apply="marketDownload" @remove="marketDelete" @like="toggleLike" @favorite="toggleFavorite"
          @edit="openMarketEdit" @update="openUploadDialog(item.id)"></ThemeCard>
      </div>
      <div v-else class="empty">{{ panel.market.loaded ? '市场里还没有主题。' : '点「刷新」加载市场。' }}</div>
    </div>
    <div class="market-pager" v-if="panel.market.total > 0">
      <span class="muted">共 {{ panel.market.total }} 个主题</span>
      <el-pagination background layout="prev, pager, next" :total="panel.market.total"
        :page-size="panel.market.pageSize" :current-page="panel.market.page" @current-change="goMarketPage"></el-pagination>
    </div>
  </section>
</template>

<script setup>
import ThemeCard from "../components/ThemeCard.vue";
import {
  panel, marketSearch, goMarketPage, marketDownload, marketDelete, toggleLike, toggleFavorite,
  openMarketEdit, openUploadDialog,
} from "../store.js";
</script>
