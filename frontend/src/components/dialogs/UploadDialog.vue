<template>
  <div v-if="panel.uploadDialog.show" class="modal" @click.self="panel.uploadDialog.show = false">
    <div class="dialog market-upload-dialog">
      <h3>上传到市场</h3>
      <label class="pfield"><span>选择项目</span>
        <el-select v-model="panel.uploadDialog.projectId" style="width: 100%">
          <el-option v-for="item in panel.overview.themes" :key="item.id" :label="item.name" :value="item.id"></el-option>
        </el-select>
      </label>
      <label class="pfield"><span>上传方式</span>
        <el-select v-model="panel.uploadDialog.targetId" style="width: 100%">
          <el-option label="新建一个市场主题" value="new"></el-option>
          <el-option v-for="item in panel.myPublished" :key="item.id" :label="'更新已有：' + item.name" :value="item.id"></el-option>
        </el-select>
      </label>
      <p class="muted">更新已有主题会覆盖它的主题包，点赞、收藏和下载数会保留。</p>
      <p v-if="panel.uploadDialog.busy" class="upload-hint">正在打包并上传到市场，视频较大时需要等一会儿，请不要关闭窗口。</p>
      <div class="dialog-actions">
        <button class="btn" type="button" :disabled="panel.uploadDialog.busy" @click="panel.uploadDialog.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="panel.uploadDialog.busy" @click="marketUpload">
          {{ !panel.market.token
            ? '登录后可上传'
            : (panel.uploadDialog.busy
              ? (panel.uploadDialog.targetId !== 'new' ? '更新中…' : '上传中…')
              : (panel.uploadDialog.targetId !== 'new' ? '更新主题包' : '上传新主题')) }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, marketUpload } from "../../store.js";
</script>
