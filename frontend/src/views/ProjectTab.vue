<template>
  <section class="tab-page">
    <div class="page-head">
      <div><h2>主题项目</h2><p class="muted">每个项目独立保存素材和画布；保存直接覆盖当前项目。</p></div>
    </div>
    <div class="project-layout">
      <div class="project-toolbar">
        <label>当前项目
          <el-select v-model="panel.projectId" style="width: 240px" @change="loadProject">
            <el-option v-for="item in panel.overview.themes" :key="item.id"
              :label="item.name + (item.id === panel.overview.active_theme_id ? '（当前应用）' : '')" :value="item.id"></el-option>
          </el-select>
        </label>
        <button class="btn" type="button" @click="createProject(false)">新建项目</button>
        <button class="btn" type="button" @click="createProject(true)" :disabled="!panel.project">复制当前</button>
        <span class="muted">{{ panel.overview.themes.length }} 个项目</span>
      </div>
      <div class="project-main" v-if="panel.project">
        <div class="project-info">
          <div class="project-cover">
            <div class="project-cover-edit">
              <img v-if="panel.project.cover_url" :src="panel.project.cover_url" alt="" @click="panel.coverPreview = panel.project.cover_url" />
              <span v-else class="cover-empty">还没有封面</span>
              <label class="cover-overlay" :class="{ always: !panel.project.cover_url }">更换封面<input type="file" accept="image/jpeg,image/png,image/webp" hidden @change="onProjectCover" /></label>
            </div>
            <small>建议 16:10，JPG / PNG / WebP</small>
          </div>
          <div class="project-fields">
            <div class="project-title-row">
              <div class="pfield grow"><span>主题名称</span><el-input v-model="panel.projectForm.name" maxlength="60" show-word-limit placeholder="主题名称"></el-input></div>
              <div class="pfield"><span>作者</span><el-input v-model="panel.projectForm.author" maxlength="40" placeholder="作者"></el-input></div>
              <div class="pfield version"><span>版本</span><el-input v-model="panel.projectForm.version" maxlength="32" placeholder="1.0.0"></el-input></div>
            </div>
            <div class="pfield"><span>介绍</span><el-input v-model="panel.projectForm.description" type="textarea" :rows="2" maxlength="2000" show-word-limit placeholder="这个主题适合什么场景、包含哪些动作"></el-input></div>
            <div class="project-actions">
              <span class="muted">保存只写入项目，点「应用为壁纸」才会覆盖线上</span>
              <div class="head-actions">
                <button class="btn" type="button" @click="saveProjectMeta">保存信息</button>
                <button class="btn" type="button" @click="exportProject">导出项目</button>
                <button class="btn primary" type="button" @click="applyProject">应用为壁纸</button>
                <button class="btn danger" type="button" @click="deleteProject">删除项目</button>
              </div>
            </div>
          </div>
        </div>
        <div class="section-title"><h2>项目素材</h2><label class="btn">上传素材<input type="file" multiple accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov,.m4v" hidden @change="onProjectAsset" /></label></div>
        <div class="project-assets" v-if="projectAssets.length">
          <div v-for="item in projectAssets" :key="item.id" class="project-asset">
            <video :src="projectMediaUrl(item.video)" muted playsinline preload="metadata"
              @click="panel.preview = { name: item.name, duration: item.duration, video: projectMediaUrl(item.video) }"></video>
            <b>{{ item.name }}</b>
            <small>{{ fmtDuration(item.duration) }}</small>
            <div class="asset-actions">
              <button class="btn small ghost" type="button" @click="renameProjectAsset(item)">改名</button>
              <label class="btn small ghost">更换<input type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov,.m4v" hidden @change="replaceProjectAsset(item, $event)" /></label>
              <button class="btn small ghost" type="button" @click="downloadProjectAsset(item)">下载</button>
              <button class="btn small danger" type="button" @click="deleteProjectAsset(item)">删除</button>
            </div>
          </div>
        </div>
        <div v-else class="empty">这个项目还没有素材。</div>
        <CanvasPanel ref="canvasRef" :key="panel.projectId" :flow="projectFlow" :state="panel.live"
          :library="projectAssets" :toast="toast" :confirm="confirm" :project-mode="true"
          title="项目画布" hint="保存只覆盖当前项目，点「应用为壁纸」才会生效；拖节点、从圆点拉线，选中后按 Delete 或右键删除。"
          @save-project="saveProjectFlow" />
      </div>
    </div>
  </section>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";
import CanvasPanel from "../components/CanvasPanel.vue";
import {
  panel, projectAssets, projectFlow, loadProject, createProject, saveProjectMeta, saveProjectFlow,
  onProjectAsset, deleteProjectAsset, renameProjectAsset, replaceProjectAsset, downloadProjectAsset,
  onProjectCover, exportProject, applyProject, deleteProject, projectMediaUrl, fmtDuration, toast, confirm,
} from "../store.js";

const canvasRef = ref(null);

function refit(delay) {
  nextTick(() => setTimeout(() => canvasRef.value?.refit(), delay));
}

// 进入项目页时没有项目就先加载当前主题的项目，有项目则重新自适应画布。
watch(() => panel.tab, (value) => {
  if (value !== "project") return;
  if (!panel.project) {
    const first = panel.overview?.active_theme_id || panel.overview?.themes?.[0]?.id;
    if (first) loadProject(first);
  } else {
    refit(80);
  }
});

// 切换项目后画布内容整体替换，等渲染完再自适应一次。
watch(() => panel.project, () => refit(200));
</script>
