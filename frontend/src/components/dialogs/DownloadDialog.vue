<template>
  <div v-if="panel.market.download.active" class="modal download-modal" @click.self="hideDownloadProgress">
    <div class="dialog download-dialog">
      <h3>{{ panel.market.download.error ? '下载失败' : '正在下载主题' }}</h3>
      <p v-if="panel.market.download.error" class="download-error">{{ panel.market.download.error }}</p>
      <template v-else>
        <p>{{ panel.market.download.phase || '下载主题包' }} · {{ downloadPercent() }}%</p>
        <div class="download-bar"><i :style="{ width: downloadPercent() + '%' }"></i></div>
        <p class="download-meta" v-if="panel.market.download.total">
          {{ (panel.market.download.received / 1048576).toFixed(1) }} / {{ (panel.market.download.total / 1048576).toFixed(1) }} MB
        </p>
        <p class="download-meta" v-else>正在连接服务器…</p>
      </template>
      <div class="dialog-actions">
        <button v-if="panel.market.download.error" class="btn" type="button" @click="hideDownloadProgress">关闭</button>
        <button v-else class="btn" type="button" @click="cancelDownload">取消下载</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, downloadPercent, cancelDownload, hideDownloadProgress } from "../../store.js";
</script>
