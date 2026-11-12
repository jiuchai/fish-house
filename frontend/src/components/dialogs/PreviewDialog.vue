<template>
  <div v-if="panel.preview" class="modal preview-modal" @click.self="panel.preview = null">
    <div class="preview-box">
      <button class="modal-close" type="button" @click="panel.preview = null">×</button>
      <video :src="mediaUrl(panel.preview.video)" controls autoplay playsinline></video>
      <div class="preview-meta"><b>{{ panel.preview.name }}</b><span>{{ fmtDuration(panel.preview.duration) }}</span></div>
    </div>
  </div>

  <div v-if="panel.coverPreview" class="modal preview-modal" @click.self="panel.coverPreview = ''">
    <div class="preview-box cover-preview-box">
      <button class="modal-close" type="button" @click="panel.coverPreview = ''">×</button>
      <img :src="panel.coverPreview" alt="封面预览" />
    </div>
  </div>
</template>

<script setup>
import { panel, mediaUrl, fmtDuration } from "../../store.js";
</script>
