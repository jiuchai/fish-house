<template>
  <section class="tab-page">
    <div class="page-head"><div><h2>设置</h2><p class="muted">播放、活动气泡和主题市场。</p></div></div>
    <div class="two-col settings-top">
      <section class="card settings-card">
        <div class="card-head"><h3>播放</h3></div>
        <div class="form-grid one">
          <label>叠化时长（毫秒）<input v-model.number="panel.settingsForm.crossfade_ms" type="number" min="0" max="2000" step="50" /></label>
        </div>
        <div class="settings-actions"><button class="btn primary" type="button" @click="saveSettings">保存设置</button></div>
      </section>
      <section class="card settings-card">
        <div class="card-head"><h3>活动气泡</h3></div>
        <p class="muted">显示在公共壁纸底部，例如「帮主人查资料」。</p>
        <div class="input-row">
          <input v-model="panel.bubbleText" placeholder="帮主人查资料" @keyup.enter="sendBubble" />
          <button class="btn warm" type="button" @click="sendBubble">发送</button>
          <button class="btn" type="button" @click="clearBubble">清空</button>
        </div>
        <div class="quick-bubbles">
          <button v-for="text in QUICK_BUBBLES" :key="text" class="quick-bubble" type="button" @click="panel.bubbleText = text">{{ text }}</button>
        </div>
      </section>
    </div>
    <section class="card">
      <div class="card-head"><h3>主题市场</h3><span class="muted">登录、下载、上传都使用这个地址</span></div>
      <div class="input-row">
        <input v-model="panel.market.url" placeholder="https://shop.swellengine.com/" @change="saveMarketUrl" />
        <button class="btn" type="button" @click="saveMarketUrl">保存地址</button>
      </div>
      <p class="muted" style="margin-top:8px">可以填任意自定义的市场地址，需要 http:// 或 https://，例如 https://market.example.com。</p>
    </section>
  </section>
</template>

<script setup>
import { panel, saveSettings, sendBubble, clearBubble, saveMarketUrl } from "../store.js";

const QUICK_BUBBLES = ["帮主人查资料", "正在干活，稍等", "准备休息啦"];
</script>
