<template>
  <div v-if="panel.conflict.show" class="modal" @click.self="panel.conflict.show = false">
    <div class="dialog">
      <h3>主题已存在</h3>
      <p>本地已有「{{ panel.conflict.existing?.name }}」。下载「{{ panel.conflict.incoming?.name }}」要怎么处理？</p>
      <label class="pfield"><span>重命名后的主题名</span><input v-model="panel.conflict.name" maxlength="60" placeholder="输入新的主题名称" /></label>
      <p class="muted">下载后不会自动应用，可到「主题」页手动应用。</p>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="resolveConflict('cancel')">取消</button>
        <button class="btn" type="button" @click="resolveConflict('rename')">重命名并下载</button>
        <button class="btn danger" type="button" @click="resolveConflict('overwrite')">覆盖下载</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, resolveConflict } from "../../store.js";
</script>
