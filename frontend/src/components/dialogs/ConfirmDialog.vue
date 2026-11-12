<template>
  <div v-if="panel.dialog.show" class="modal" @click.self="dialogCancel">
    <div class="dialog">
      <h3>{{ panel.dialog.title }}</h3>
      <p>{{ panel.dialog.text }}</p>
      <input v-if="panel.dialog.mode === 'prompt'" v-model="panel.dialog.value" @keyup.enter="dialogOk" />
      <div class="dialog-actions">
        <button class="btn" type="button" @click="dialogCancel">取消</button>
        <button class="btn" :class="panel.dialog.danger ? 'danger' : 'primary'" type="button" @click="dialogOk">确定</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, dialogOk, dialogCancel } from "../../store.js";
</script>
