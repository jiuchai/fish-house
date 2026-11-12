<template>
  <div v-if="panel.editDialog.show" class="modal" @click.self="panel.editDialog.show = false">
    <div class="dialog market-edit-dialog">
      <h3>编辑市场信息</h3>
      <label class="pfield"><span>主题名称</span><input v-model="panel.editDialog.name" maxlength="60" /></label>
      <label class="pfield"><span>版本</span><input v-model="panel.editDialog.version" maxlength="32" /></label>
      <label class="pfield"><span>介绍</span><textarea v-model="panel.editDialog.description" rows="3" maxlength="2000"></textarea></label>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="panel.editDialog.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="panel.editDialog.busy" @click="saveMarketInfo">保存</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, saveMarketInfo } from "../../store.js";
</script>
