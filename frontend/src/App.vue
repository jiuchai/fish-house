<template>
  <header class="app-header">
    <div class="brand">
      <div class="brand-mark">屋</div>
      <div>
        <h1>大肥鱼的小屋</h1>
      </div>
    </div>
    <div class="header-right">
      <span class="status-pill" :class="'mode-' + (state.mode || 'rest')">
        <i></i>{{ modeLabel(state.mode) }}
      </span>
      <button v-if="!market.token" class="btn small" type="button" @click="openAuth('login')">登录</button>
      <span v-else class="user-pill" @click="tab = 'my'">{{ market.user }}</span>
      <button class="icon-btn" type="button" title="刷新" @click="load" :disabled="loading">⟳</button>
      <span class="version">v{{ overview?.version || '—' }}</span>
    </div>
  </header>

  <nav class="tabs" aria-label="面板导航">
    <button v-for="item in tabs" :key="item.key" type="button"
      class="tab" :class="{ active: tab === item.key }" @click="tab = item.key">
      <span v-html="item.icon"></span>{{ item.label }}
    </button>
  </nav>

  <div v-if="error" class="banner error">
    <b>接口请求失败：</b>{{ error }}
    <button class="btn ghost small" type="button" @click="load">重试</button>
  </div>

  <main v-if="overview" class="content">
    <!-- 概览 -->
    <section v-show="tab === 'overview'" class="tab-page">
      <div class="home-top">
        <div class="hero-card" :class="{ 'has-cover': activeTheme && activeTheme.cover_url }" :style="heroStyle" @click="activeTheme?.cover_url && (coverPreview = activeTheme.cover_url)">
          <div class="hero-theme">
            <p class="eyebrow">CURRENT THEME</p>
            <h2>{{ activeTheme?.name || overview.theme_meta.name || '我的主题' }}</h2>
            <p class="hero-sub">{{ activeTheme?.description || overview.theme_meta.description || '还没有写主题介绍。' }}</p>
            <div class="hero-meta">
              <span>{{ activeTheme?.author || overview.theme_meta.author || '匿名作者' }}</span>
              <span>v{{ activeTheme?.version || overview.theme_meta.version || '1.0.0' }}</span>
              <span class="hero-status">当前：{{ modeLabel(state.mode) }}</span>
            </div>
          </div>
          <div class="hero-stats">
            <div><b>{{ overview.library.length }}</b><span>素材</span></div>
            <div><b>{{ overview.flow.edges.length }}</b><span>连线</span></div>
            <div><b>{{ overview.flow.nodes.length }}</b><span>节点</span></div>
            <div><b>{{ overview.themes.length }}</b><span>主题</span></div>
          </div>
        </div>

        <section class="card">
          <div class="card-head"><h3>壁纸链接</h3><span class="tag" :class="overview.public_ready ? 'good' : 'warn'">{{ overview.public_ready ? '已同步' : '未同步' }}</span></div>
          <div class="input-row">
            <input :value="publicUrl" readonly />
            <button class="btn" type="button" @click="copy(publicUrl)">复制</button>
          </div>
          <label class="switch-line">显示底部气泡<el-switch v-model="settingsForm.bubble_enabled" @change="toggleBubble" /></label>
        </section>
      </div>

      <section class="card home-flow">
        <div class="card-head"><h3>当前画布</h3><button class="btn ghost small" type="button" @click="tab = 'project'">编辑项目</button></div>
        <canvas-panel readonly :flow="overview.flow" :state="state" :library="overview.library" :toast="toast" :confirm="confirm"></canvas-panel>
      </section>

    </section>

    <!-- 主题 -->
    <section v-show="tab === 'themes'" class="tab-page">
      <div class="page-head">
        <div><h2>主题</h2><p class="muted">本地主题库；编辑会进入项目页，应用会覆盖当前壁纸配置。</p></div>
        <div class="head-actions">
          <label class="btn">导入主题<input type="file" accept=".fishtheme,.zip" hidden @change="onThemeImport" /></label>
          <button class="btn" type="button" @click="tab = 'project'; createProject(false)">新建主题</button>
        </div>
      </div>
      <div class="theme-grid" v-if="overview.themes.length">
        <theme-card v-for="item in overview.themes" :key="item.id" :item="item"
          :active="item.id === overview.active_theme_id"
          @apply="applyTheme" @remove="deleteTheme" @edit="openThemeProject"></theme-card>
      </div>
      <div v-else class="empty">还没有主题，点「新建主题」或从市场下载。</div>
    </section>

    <!-- 市场 -->
    <section v-show="tab === 'market'" class="tab-page market-page">
      <div class="page-head market-head">
        <div><h2>主题市场</h2><p class="muted">浏览、下载并应用别人的主题，或上传自己的配置；登录后可上传自己的主题。</p></div>
        <div class="market-head-search">
          <input v-model="market.query" class="search" placeholder="搜索主题 / 作者" @keyup.enter="marketSearch()" />
          <button class="btn" type="button" @click="marketSearch()">搜索</button>
        </div>
        <div class="head-actions">
          <button class="btn" type="button" @click="marketSearch()" :disabled="market.busy">刷新</button>
          <button class="btn primary" type="button" @click="openUploadDialog()" :disabled="market.busy || !market.token">上传 / 更新</button>
        </div>
      </div>
      <p v-if="market.message" class="market-inline-msg">{{ market.message }}</p>
      <div class="market-scroll">
        <div class="theme-grid" v-if="market.items.length">
          <theme-card v-for="item in market.items" :key="item.id" :item="item" market @apply="marketDownload" @remove="marketDelete" @like="toggleLike" @favorite="toggleFavorite" @edit="openMarketEdit" @update="openUploadDialog(item.id)"></theme-card>
        </div>
        <div v-else class="empty">{{ market.loaded ? '市场里还没有主题。' : '点「刷新」加载市场。' }}</div>
      </div>
      <div class="market-pager" v-if="market.total > 0">
        <span class="muted">共 {{ market.total }} 个主题</span>
        <el-pagination background layout="prev, pager, next" :total="market.total"
          :page-size="market.pageSize" :current-page="market.page" @current-change="goMarketPage"></el-pagination>
      </div>
    </section>

    <!-- 项目 -->
    <section v-show="tab === 'project'" class="tab-page">
      <div class="page-head">
        <div><h2>主题项目</h2><p class="muted">每个项目独立保存素材和画布；保存直接覆盖当前项目。</p></div>
      </div>
      <div class="project-layout">
        <div class="project-toolbar">
          <label>当前项目
            <el-select v-model="projectId" style="width: 240px" @change="loadProject">
              <el-option v-for="item in overview.themes" :key="item.id" :label="item.name + (item.id === overview.active_theme_id ? '（当前应用）' : '')" :value="item.id"></el-option>
            </el-select>
          </label>
          <button class="btn" type="button" @click="createProject(false)">新建项目</button>
          <button class="btn" type="button" @click="createProject(true)" :disabled="!project">复制当前</button>
          <span class="muted">{{ overview.themes.length }} 个项目</span>
        </div>
        <div class="project-main" v-if="project">
          <div class="project-info">
            <div class="project-cover">
              <div class="project-cover-edit">
                <img v-if="project.cover_url" :src="project.cover_url" alt="" @click="coverPreview = project.cover_url" />
                <span v-else class="cover-empty">还没有封面</span>
                <label class="cover-overlay" :class="{ always: !project.cover_url }">更换封面<input type="file" accept="image/jpeg,image/png,image/webp" hidden @change="onProjectCover" /></label>
              </div>
              <small>建议 16:10，JPG / PNG / WebP</small>
            </div>
            <div class="project-fields">
              <div class="project-title-row">
                <div class="pfield grow"><span>主题名称</span><el-input v-model="projectForm.name" maxlength="60" show-word-limit placeholder="主题名称"></el-input></div>
                <div class="pfield"><span>作者</span><el-input v-model="projectForm.author" maxlength="40" placeholder="作者"></el-input></div>
                <div class="pfield version"><span>版本</span><el-input v-model="projectForm.version" maxlength="32" placeholder="1.0.0"></el-input></div>
              </div>
              <div class="pfield"><span>介绍</span><el-input v-model="projectForm.description" type="textarea" :rows="2" maxlength="2000" show-word-limit placeholder="这个主题适合什么场景、包含哪些动作"></el-input></div>
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
              <video :src="projectMediaUrl(item.video)" muted playsinline preload="metadata" @click="preview = { name: item.name, duration: item.duration, video: projectMediaUrl(item.video) }"></video>
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
          <canvas-panel ref="projectCanvas" :key="projectId" :flow="projectFlow" :state="state" :library="projectAssets" :toast="toast" :confirm="confirm"
            :project-mode="true" title="项目画布" hint="保存只覆盖当前项目，点「应用为壁纸」才会生效；拖节点、从圆点拉线，选中后按 Delete 或右键删除。" @save-project="saveProjectFlow"></canvas-panel>
        </div>
      </div>
    </section>

    <!-- 我的 -->
    <section v-show="tab === 'my'" class="tab-page">
      <div class="page-head"><div><h2>我的</h2><p class="muted">发布过的、收藏和点赞的主题。</p></div></div>
      <section v-if="!market.token" class="card login-cta">
        <div><b>还没有登录</b><p class="muted">登录后可以查看你发布的主题、收藏和点赞。</p></div>
        <button class="btn primary" type="button" @click="openAuth('login')">登录 / 注册</button>
      </section>
      <template v-else>
        <section class="card profile-card">
          <div><b>{{ market.user }}</b><p class="muted">已登录主题市场</p></div>
          <button class="btn ghost small" type="button" @click="logout">退出</button>
        </section>
        <div class="my-tabs">
          <button type="button" :class="{ active: myKind === 'published' }" @click="myKind = 'published'; loadMy()">我发布的</button>
          <button type="button" :class="{ active: myKind === 'favorites' }" @click="myKind = 'favorites'; loadMy()">收藏</button>
          <button type="button" :class="{ active: myKind === 'likes' }" @click="myKind = 'likes'; loadMy()">点赞</button>
        </div>
        <div class="theme-grid" v-if="myItems.length">
          <theme-card v-for="item in myItems" :key="item.id" :item="item" market
            :show-actions="myKind === 'published'" @apply="marketDownload" @remove="marketDelete"
            @like="toggleLike" @favorite="toggleFavorite" @edit="openMarketEdit" @update="openUploadDialog(item.id)"></theme-card>
        </div>
        <div v-else class="empty">这里还没有内容。</div>
      </template>
    </section>

    <!-- 设置 -->
    <section v-show="tab === 'settings'" class="tab-page">
      <div class="page-head"><div><h2>设置</h2><p class="muted">播放、活动气泡和主题市场。</p></div></div>
      <div class="two-col settings-top">
        <section class="card settings-card">
          <div class="card-head"><h3>播放</h3></div>
          <div class="form-grid one">
            <label>叠化时长（毫秒）<input v-model.number="settingsForm.crossfade_ms" type="number" min="0" max="2000" step="50" /></label>
          </div>
          <div class="settings-actions"><button class="btn primary" type="button" @click="saveSettings">保存设置</button></div>
        </section>
        <section class="card settings-card">
          <div class="card-head"><h3>活动气泡</h3></div>
          <p class="muted">显示在公共壁纸底部，例如「帮主人查资料」。</p>
          <div class="input-row"><input v-model="bubbleText" placeholder="帮主人查资料" @keyup.enter="sendBubble" /><button class="btn warm" type="button" @click="sendBubble">发送</button><button class="btn" type="button" @click="clearBubble">清空</button></div>
          <div class="quick-bubbles">
            <button v-for="text in ['帮主人查资料', '正在干活，稍等', '准备休息啦']" :key="text" class="quick-bubble" type="button" @click="bubbleText = text">{{ text }}</button>
          </div>
        </section>
      </div>
      <section class="card">
        <div class="card-head"><h3>主题市场</h3><span class="muted">登录、下载、上传都使用这个地址</span></div>
        <div class="input-row">
          <input v-model="market.url" placeholder="https://shop.swellengine.com/" @change="saveMarketUrl" />
          <button class="btn" type="button" @click="saveMarketUrl">保存地址</button>
        </div>
        <p class="muted" style="margin-top:8px">可以填任意自定义的市场地址，需要 http:// 或 https://，例如 https://market.example.com。</p>
      </section>
    </section>
  </main>

  <div v-else-if="loading" class="loading"><span class="spinner"></span>正在读取插件数据…</div>

  <footer class="footer">© 2026 大肥鱼的小屋 · jiuchai · v{{ overview?.version || '' }}</footer>

  <div v-if="toastData.show" class="toast show" :class="toastData.type">{{ toastData.text }}</div>

  <div v-if="preview" class="modal preview-modal" @click.self="preview = null">
    <div class="preview-box">
      <button class="modal-close" type="button" @click="preview = null">×</button>
      <video :src="mediaUrl(preview.video)" controls autoplay playsinline></video>
      <div class="preview-meta"><b>{{ preview.name }}</b><span>{{ fmtDuration(preview.duration) }}</span></div>
    </div>
  </div>

  <div v-if="coverPreview" class="modal preview-modal" @click.self="coverPreview = ''">
    <div class="preview-box cover-preview-box">
      <button class="modal-close" type="button" @click="coverPreview = ''">×</button>
      <img :src="coverPreview" alt="封面预览" />
    </div>
  </div>

  <div v-if="uploadDialog.show" class="modal" @click.self="uploadDialog.show = false">
    <div class="dialog market-upload-dialog">
      <h3>上传到市场</h3>
      <label class="pfield"><span>选择项目</span>
        <el-select v-model="uploadDialog.projectId" style="width: 100%">
          <el-option v-for="item in overview.themes" :key="item.id" :label="item.name" :value="item.id"></el-option>
        </el-select>
      </label>
      <label class="pfield"><span>上传方式</span>
        <el-select v-model="uploadDialog.targetId" style="width: 100%">
          <el-option label="新建一个市场主题" value="new"></el-option>
          <el-option v-for="item in myPublished" :key="item.id" :label="'更新已有：' + item.name" :value="item.id"></el-option>
        </el-select>
      </label>
      <p class="muted">更新已有主题会覆盖它的主题包，点赞、收藏和下载数会保留。</p>
      <p v-if="uploadDialog.busy" class="upload-hint">正在打包并上传到市场，视频较大时需要等一会儿，请不要关闭窗口。</p>
      <div class="dialog-actions">
        <button class="btn" type="button" :disabled="uploadDialog.busy" @click="uploadDialog.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="uploadDialog.busy" @click="marketUpload">
          {{ !market.token
            ? '登录后可上传'
            : (uploadDialog.busy
              ? (uploadDialog.targetId !== 'new' ? '更新中…' : '上传中…')
              : (uploadDialog.targetId !== 'new' ? '更新主题包' : '上传新主题')) }}
        </button>
      </div>
    </div>
  </div>

  <div v-if="editDialog.show" class="modal" @click.self="editDialog.show = false">
    <div class="dialog market-edit-dialog">
      <h3>编辑市场信息</h3>
      <label class="pfield"><span>主题名称</span><input v-model="editDialog.name" maxlength="60" /></label>
      <label class="pfield"><span>版本</span><input v-model="editDialog.version" maxlength="32" /></label>
      <label class="pfield"><span>介绍</span><textarea v-model="editDialog.description" rows="3" maxlength="2000"></textarea></label>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="editDialog.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="editDialog.busy" @click="saveMarketInfo">保存</button>
      </div>
    </div>
  </div>

  <div v-if="auth.show" class="modal" @click.self="auth.show = false">
    <div class="dialog auth-modal">
      <h3>主题市场账号</h3>
      <div class="auth-tabs">
        <button type="button" :class="{ active: auth.mode === 'login' }" @click="auth.mode = 'login'">登录</button>
        <button type="button" :class="{ active: auth.mode === 'register' }" @click="auth.mode = 'register'">注册</button>
      </div>
      <input v-model="auth.email" v-if="auth.mode === 'register'" type="email" placeholder="邮箱（唯一 ID）" autocomplete="email" />
      <input v-model="auth.username" :placeholder="auth.mode === 'login' ? '用户名或邮箱' : '用户名'" autocomplete="username" />
      <input v-model="auth.password" type="password" placeholder="密码" autocomplete="current-password" @keyup.enter="submitAuth" />
      <p v-if="auth.error" class="auth-error">{{ auth.error }}</p>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="auth.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="auth.busy" @click="submitAuth">{{ auth.mode === 'login' ? '登录' : '注册' }}</button>
      </div>
    </div>
  </div>

  <div v-if="conflict.show" class="modal" @click.self="conflict.show = false">
    <div class="dialog">
      <h3>主题已存在</h3>
      <p>本地已有「{{ conflict.existing?.name }}」。下载「{{ conflict.incoming?.name }}」要怎么处理？</p>
      <label class="pfield"><span>重命名后的主题名</span><input v-model="conflict.name" maxlength="60" placeholder="输入新的主题名称" /></label>
      <p class="muted">下载后不会自动应用，可到「主题」页手动应用。</p>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="resolveConflict('cancel')">取消</button>
        <button class="btn" type="button" @click="resolveConflict('rename')">重命名并下载</button>
        <button class="btn danger" type="button" @click="resolveConflict('overwrite')">覆盖下载</button>
      </div>
    </div>
  </div>

  <div v-if="market.download.active" class="modal download-modal" @click.self="hideDownloadProgress">
    <div class="dialog download-dialog">
      <h3>{{ market.download.error ? '下载失败' : '正在下载主题' }}</h3>
      <p v-if="market.download.error" class="download-error">{{ market.download.error }}</p>
      <template v-else>
        <p>{{ market.download.phase || '下载主题包' }} · {{ downloadPercent() }}%</p>
        <div class="download-bar"><i :style="{ width: downloadPercent() + '%' }"></i></div>
        <p class="download-meta" v-if="market.download.total">
          {{ (market.download.received / 1048576).toFixed(1) }} / {{ (market.download.total / 1048576).toFixed(1) }} MB
        </p>
        <p class="download-meta" v-else>正在连接服务器…</p>
      </template>
      <div class="dialog-actions">
        <button v-if="market.download.error" class="btn" type="button" @click="hideDownloadProgress">关闭</button>
        <button v-else class="btn" type="button" @click="cancelDownload">取消下载</button>
      </div>
    </div>
  </div>

  <div v-if="dialog.show" class="modal" @click.self="dialogCancel">
    <div class="dialog">
      <h3>{{ dialog.title }}</h3>
      <p>{{ dialog.text }}</p>
      <input v-if="dialog.mode === 'prompt'" v-model="dialog.value" @keyup.enter="dialogOk" />
      <div class="dialog-actions">
        <button class="btn" type="button" @click="dialogCancel">取消</button>
        <button class="btn" :class="dialog.danger ? 'danger' : 'primary'" type="button" @click="dialogOk">确定</button>
      </div>
    </div>
  </div>
</template>

<script>
import { apiGet, apiPost, bufferToBase64, storageGet, storageSet, THEME_TOKEN_KEY, THEME_MARKET_KEY, THEME_USER_KEY } from "./api.js";
import { TABS, ROLE_LABELS, MODE_LABELS } from "./constants.js";

export default {
  data() {
    return {
      loading: true, error: "", tab: "overview", busy: false,
      overview: null, state: { mode: "flow", bubble: "" },
      libraryQuery: "", preview: null, coverPreview: "",
      toastData: { show: false, text: "", type: "" },
      dialog: { show: false, title: "", text: "", mode: "confirm", value: "", danger: false },
      settingsForm: { crossfade_ms: 400, bubble_enabled: true },
      themeForm: { name: "我的主题", author: "", version: "1.0.0", description: "" },
      market: { url: "", user: "", pass: "", token: "", query: "", items: [], loaded: false, busy: false, cancelling: false, message: "", page: 1, pageSize: 12, total: 0, download: { active: false, phase: "", received: 0, total: 0, error: "" }, poll: 0 },
      backups: [], backupId: "", bubbleText: "",
      projectId: "", project: null, projectBase: "", projectBusy: false,
      projectForm: { name: "", author: "", version: "1.0.0", description: "" },
      auth: { mode: "login", username: "", password: "", email: "", busy: false, show: false, error: "" },
      myKind: "published", myItems: [], myLoaded: false,
      uploadDialog: { show: false, projectId: "", targetId: "", busy: false },
      editDialog: { show: false, id: "", name: "", description: "", version: "", busy: false },
      myPublished: [],
      conflict: { show: false, incoming: null, existing: null },
      tabs: TABS,
    };
  },
  computed: {
    activeTheme() {
      const themes = this.overview?.themes || [];
      return themes.find((item) => item.id === this.overview?.active_theme_id) || themes[0] || null;
    },
    heroStyle() {
      const cover = this.activeTheme?.cover_url;
      return cover ? { backgroundImage: `url("${cover}")` } : {};
    },
    publicUrl() {
      if (this.overview?.public_url) return this.overview.public_url;
      return `${window.location.origin}/${this.overview?.prefix || "fish_house"}/index.html`;
    },
    usage() {
      const counts = {};
      for (const node of this.overview?.flow?.nodes || []) {
        if (node.media_id) counts[node.media_id] = (counts[node.media_id] || 0) + 1;
      }
      return counts;
    },
    projectAssets() {
      return this.project?.theme?.config?.library || [];
    },
    projectFlow() {
      return this.project?.theme?.config?.flow || { nodes: [], edges: [] };
    },
    filteredLibrary() {
      const items = this.overview?.library || [];
      const q = this.libraryQuery.trim().toLowerCase();
      return q ? items.filter((item) => String(item.name).toLowerCase().includes(q)) : items;
    },
    currentNode() {
      const nodes = this.overview?.flow?.nodes || [];
      const nodeId = this.state?.node_id;
      return nodeId ? nodes.find((n) => n.id === nodeId) || null : null;
    },
  },
  async mounted() {
    await this.load();
    this.connectLive();
    this.loadBackups();
    window.addEventListener("wheel", this.onShellWheel, { passive: false });
  },
  beforeUnmount() {
    this.closeLive();
    window.removeEventListener("wheel", this.onShellWheel);
  },
  watch: {
    project() {
      this.$nextTick(() => setTimeout(() => this.$refs.projectCanvas?.refit(), 200));
    },
    tab(value) {
      if (value === "settings") this.loadBackups();
      if (value === "market" && !this.market.loaded) this.marketSearch();
      if (value === "canvas") this.$nextTick(() => setTimeout(() => this.$refs.canvasPanel?.refit(), 80));
      if (value === "project") {
        if (this.project) this.$nextTick(() => setTimeout(() => this.$refs.projectCanvas?.refit(), 80));
        else {
          const first = this.overview?.active_theme_id || this.overview?.themes?.[0]?.id;
          if (first) this.loadProject(first);
        }
      }
      if (value === "my") this.loadMy();
    },
  },
  methods: {
    onShellWheel(event) {
      if (event.target?.closest?.(".canvas, .modal, .el-popper, .el-select-dropdown, video")) return;
      const content = this.$el.querySelector(".content");
      if (!content || !event.deltaY) return;
      content.scrollTop += event.deltaY;
      event.preventDefault();
    },
    modeLabel(mode) { return MODE_LABELS[mode] || mode || "休息"; },
    roleLabel(role) { return ROLE_LABELS[role] || "动作"; },
    fmtDuration(value) { const n = Number(value || 0); return n > 0 ? `${n.toFixed(1)}s` : "时长未知"; },
    mediaUrl(value) {
      const url = String(value || "");
      if (!url) return "";
      if (/^(https?:|data:|blob:)/.test(url) || url.startsWith("/")) return url;
      return `/${this.overview?.prefix || "fish_house"}/${url.replace(/^\.?\//, "")}`;
    },
    mediaName(node) {
      const item = (this.overview?.library || []).find((x) => x.id === node?.media_id);
      return item ? item.name : String(node?.video || "").split("/").pop() || "未配置视频";
    },
    async load() {
      this.loading = true;
      try {
        const data = await apiGet("overview");
        this.overview = data;
        this.state = data.state || {};
        this.settingsForm = {
          crossfade_ms: Number(data.settings?.crossfade_ms ?? 400),
          bubble_enabled: Number(data.settings?.bubble_enabled ?? 1) !== 0,
        };
        this.themeForm = {
          name: data.theme_meta?.name || "我的主题",
          author: data.theme_meta?.author || "",
          version: data.theme_meta?.version || "1.0.0",
          description: data.theme_meta?.description || "",
        };
        if (!this.market.url) {
          this.market.url = data.theme_market_url || storageGet(THEME_MARKET_KEY) || "https://shop.swellengine.com/";
        }
        this.market.token = storageGet(THEME_TOKEN_KEY) || "";
        this.market.user = storageGet(THEME_USER_KEY) || "";
        this.error = "";
        if (!this.market.token) this.restoreMarketSession();
      } catch (error) {
        this.error = error.message || String(error);
      } finally {
        this.loading = false;
      }
    },
    async pollState() {
      try {
        const data = await apiGet("state");
        this.applyState(data.state);
      } catch (_) {}
    },
    applyState(state) {
      if (!state) return;
      this.state = state;
      if (this.overview) this.overview.state = state;
    },
    connectLive() {
      this.closeLive();
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      let socket;
      try {
        socket = new WebSocket(`${protocol}//${location.host}/ws`);
      } catch (_) {
        this.startPolling();
        return;
      }
      this.socket = socket;
      socket.onopen = () => this.stopPolling();
      socket.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch (_) { return; }
        if (message.type === "state") this.applyState(message.state);
      };
      socket.onclose = () => {
        if (this.socket !== socket) return;
        this.socket = null;
        this.startPolling();
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connectLive(), 3000);
      };
      socket.onerror = () => {};
    },
    startPolling() {
      if (this.poller) return;
      this.poller = setInterval(() => this.pollState(), 1000);
    },
    stopPolling() {
      if (!this.poller) return;
      clearInterval(this.poller);
      this.poller = null;
    },
    closeLive() {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      if (this.socket) {
        const socket = this.socket;
        this.socket = null;
        try { socket.close(); } catch (_) {}
      }
      this.stopPolling();
    },
    toast(text, type = "") {
      this.toastData = { show: true, text, type };
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { this.toastData.show = false; }, 2600);
    },
    ask(title, text, options = {}) {
      return new Promise((resolve) => {
        this.dialog = {
          show: true, title, text,
          mode: options.mode || "confirm",
          value: options.value || "",
          danger: Boolean(options.danger),
        };
        this._dialogResolve = resolve;
      });
    },
    confirm(title, text, danger = false) { return this.ask(title, text, { danger }); },
    prompt(title, text, value = "") { return this.ask(title, text, { mode: "prompt", value }); },
    dialogOk() {
      const value = this.dialog.mode === "prompt" ? this.dialog.value : true;
      this.dialog.show = false;
      const resolve = this._dialogResolve; this._dialogResolve = null;
      if (resolve) resolve(value);
    },
    dialogCancel() {
      this.dialog.show = false;
      const resolve = this._dialogResolve; this._dialogResolve = null;
      if (resolve) resolve(this.dialog.mode === "prompt" ? null : false);
    },
    async copy(text) {
      try { await navigator.clipboard.writeText(text); }
      catch (_) {
        const area = document.createElement("textarea");
        area.value = text; document.body.appendChild(area); area.select();
        document.execCommand("copy"); area.remove();
      }
      this.toast("已复制");
    },
    openUrl(url) { window.open(url, "_blank", "noopener"); },
    downloadUrl(url, filename) {
      const link = document.createElement("a");
      link.href = url;
      link.download = filename || "theme.zip";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
    },
    async trigger(action) {
      try {
        const data = await apiPost("event", { action });
        this.state = data.state || this.state;
        this.toast(`已切换：${this.modeLabel(this.state.mode)}`);
      } catch (error) { this.toast(`切换失败：${error.message || error}`, "err"); }
    },
    async sync() {
      try { await apiPost("sync", {}); this.toast("公共页已重新同步"); }
      catch (error) { this.toast(`同步失败：${error.message || error}`, "err"); }
    },
    async uploadFile(file, extra) {
      const start = await apiPost("upload/start", { filename: file.name, size: file.size, ...extra });
      const chunkSize = Number(start.chunk_size || 512 * 1024);
      const total = Math.max(1, Math.ceil(file.size / chunkSize));
      for (let index = 0; index < total; index += 1) {
        const begin = index * chunkSize;
        const end = Math.min(file.size, begin + chunkSize);
        const buffer = await file.slice(begin, end).arrayBuffer();
        await apiPost("upload/chunk", { upload_id: start.upload_id, index, data: bufferToBase64(buffer) });
      }
      return apiPost("upload/finish", { upload_id: start.upload_id });
    },
    async onLibraryUpload(event) {
      const files = [...(event.target.files || [])]; event.target.value = "";
      for (const file of files) {
        try { await this.uploadFile(file, { library: true }); this.toast(`已加入素材库：${file.name}`); }
        catch (error) { this.toast(`上传失败：${error.message || error}`, "err"); }
      }
      await this.load();
    },
    async renameMedia(item) {
      const name = await this.prompt("素材改名", "输入新的素材名", item.name);
      if (!name) return;
      try {
        const data = await apiPost("library", { action: "rename", id: item.id, name });
        if (this.overview) this.overview.library = data.library || this.overview.library;
        this.toast("素材已改名");
      } catch (error) { this.toast(`改名失败：${error.message || error}`, "err"); }
    },
    nodesUsing(nodes, mediaId) {
      return (nodes || [])
        .filter((node) => String(node.media_id || "") === String(mediaId))
        .map((node) => node.label || node.id);
    },
    async deleteMedia(item) {
      const used = this.nodesUsing(this.overview?.flow?.nodes, item.id);
      if (used.length) {
        this.toast(`素材「${item.name}」还被节点使用：${used.join("、")}，不能删除`, "err");
        return;
      }
      if (!(await this.confirm("删除素材", `从素材库删除「${item.name}」？`, true))) return;
      try {
        const data = await apiPost("library", { action: "delete", id: item.id });
        if (this.overview) this.overview.library = data.library || this.overview.library;
        this.toast("素材已删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
    },
    projectMediaUrl(video) {
      const url = String(video || "");
      if (!url) return "";
      if (/^(https?:|data:|blob:)/.test(url) || url.startsWith("/")) return url;
      return `${this.projectBase}/${url.replace(/^\.?\//, "")}`;
    },
    async loadProject(id) {
      if (!id) return;
      this.projectBusy = true;
      try {
        const data = await apiPost("theme/project", { action: "load", id });
        this.projectId = id;
        this.project = data.project;
        this.projectBase = data.project.public_base || "";
        const meta = data.project.theme.meta || {};
        this.projectForm = {
          name: meta.name || "", author: meta.author || "",
          version: meta.version || "1.0.0", description: meta.description || "",
        };
        this.$nextTick(() => setTimeout(() => this.$refs.projectCanvas?.refit(), 80));
      } catch (error) { this.toast(`读取项目失败：${error.message || error}`, "err"); }
      finally { this.projectBusy = false; }
    },
    async createProject(copy) {
      const name = await this.prompt(copy ? "复制项目" : "新建项目", "给新主题起个名字", copy && this.project ? `${this.projectForm.name} 副本` : "新主题");
      if (!name) return;
      try {
        const data = await apiPost("theme/project", copy
          ? { action: "duplicate", id: this.projectId, name }
          : { action: "create", name, author: this.themeForm.author });
        await this.load();
        await this.loadProject(data.project.id);
        this.toast(copy ? "项目已复制" : "项目已创建");
      } catch (error) { this.toast(`创建失败：${error.message || error}`, "err"); }
    },
    async saveProjectMeta() {
      if (!this.project) return;
      try {
        const data = await apiPost("theme/project", {
          action: "save", id: this.projectId,
          meta: this.projectForm,
          config: this.project.theme.config,
        });
        this.project = data.project;
        await this.load();
        this.toast("项目已保存");
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async saveProjectFlow(flow) {
      if (!this.project) return;
      this.project.theme.config.flow = flow;
      try {
        const data = await apiPost("theme/project", {
          action: "save", id: this.projectId,
          meta: this.projectForm,
          config: this.project.theme.config,
        });
        this.project = data.project;
        this.toast("画布已保存到项目");
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async onProjectAsset(event) {
      const files = [...(event.target.files || [])]; event.target.value = "";
      if (!this.project) return;
      for (const file of files) {
        try {
          const data = await this.uploadFile(file, { target: "project_asset", project_id: this.projectId });
          this.project = data.project;
          this.projectBase = data.project.public_base || this.projectBase;
          this.toast(`素材已加入：${file.name}`);
        } catch (error) { this.toast(`上传失败：${error.message || error}`, "err"); }
      }
    },
    async deleteProjectAsset(item) {
      const used = this.nodesUsing(this.project?.theme?.config?.flow?.nodes, item.id);
      if (used.length) {
        this.toast(`素材「${item.name}」还被节点使用：${used.join("、")}，不能删除`, "err");
        return;
      }
      if (!(await this.confirm("删除素材", `删除项目素材「${item.name}」？`, true))) return;
      try {
        const data = await apiPost("theme/project", { action: "asset_delete", id: this.projectId, media_id: item.id });
        this.project = data.project;
        this.toast("素材已删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
    },
    async renameProjectAsset(item) {
      if (!this.project) return;
      let name = await this.prompt("素材改名", "输入新的素材名", item.name);
      if (!name) return;
      // 名字没改时加随机后缀，避免和原来的名字完全一样。
      if (name === item.name) name = `${name}-${Math.random().toString(36).slice(2, 6)}`;
      try {
        const data = await apiPost("theme/project", { action: "asset_rename", id: this.projectId, media_id: item.id, name });
        this.project = data.project;
        this.toast("素材已改名");
      } catch (error) { this.toast(`改名失败：${error.message || error}`, "err"); }
    },
    async replaceProjectAsset(item, event) {
      const file = event.target.files?.[0]; event.target.value = "";
      if (!file || !this.project) return;
      try {
        const data = await this.uploadFile(file, { target: "project_asset", project_id: this.projectId, replace_id: item.id });
        this.project = data.project;
        this.toast(`已更换「${item.name}」的视频`);
      } catch (error) { this.toast(`更换失败：${error.message || error}`, "err"); }
    },
    async downloadProjectAsset(item) {
      const url = this.projectMediaUrl(item.video);
      if (!url) return;
      const ext = ((url.match(/\.(mp4|webm|m4v|mov)(?:\?|$)/i) || [".mp4"])[0]).replace(/\?.*$/, "");
      try {
        // 用 blob 下载，避免跨源时 download 属性失效变成直接打开视频。
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = href;
        a.download = `${item.name || "素材"}${ext}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 4000);
        this.toast(`已下载：${item.name || "素材"}`);
      } catch (error) {
        window.open(url, "_blank");
        this.toast(`浏览器未能直接下载，已在新窗口打开：${error.message || error}`, "err");
      }
    },
    async onProjectCover(event) {
      const file = event.target.files?.[0]; event.target.value = "";
      if (!file || !this.project) return;
      try {
        const data = await this.uploadFile(file, { target: "theme_cover", project_id: this.projectId });
        this.project = data.project;
        this.toast("封面已更新");
      } catch (error) { this.toast(`封面上传失败：${error.message || error}`, "err"); }
    },
    async exportProject() {
      if (!this.project) return;
      try {
        const data = await apiPost("theme/project", { action: "export", id: this.projectId });
        if (data.url) {
          this.downloadUrl(data.url, data.filename || `${this.projectId}.zip`);
          this.toast("项目已导出");
        }
        else this.toast("导出成功，但公共目录不可用", "err");
      } catch (error) { this.toast(`导出失败：${error.message || error}`, "err"); }
    },
    async applyProject() {
      if (!this.project) return;
      if (!(await this.confirm("应用项目", "把当前项目应用为壁纸？会覆盖当前素材和画布。", true))) return;
      try { await apiPost("theme", { action: "apply", id: this.projectId }); await this.load(); this.toast("项目已应用"); }
      catch (error) { this.toast(`应用失败：${error.message || error}`, "err"); }
    },
    async deleteProject() {
      if (!this.project) return;
      if (!(await this.confirm("删除项目", `删除项目「${this.projectForm.name}」？视频文件会一起删除。`, true))) return;
      try {
        await apiPost("theme/project", { action: "delete", id: this.projectId });
        this.project = null; this.projectId = "";
        await this.load();
        this.toast("项目已删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
    },
    logout() {
      apiPost("theme/market", { action: "session_clear" }).catch(() => {});
      this.market.token = ""; this.market.user = ""; this.myItems = [];
      storageSet(THEME_TOKEN_KEY, "");
      this.auth.show = false;
    },
    openThemeProject(id) {
      this.tab = "project";
      this.loadProject(id);
    },
    openAuth(mode = "login") {
      this.auth.mode = mode;
      this.auth.error = "";
      this.auth.show = true;
    },
    async submitAuth() {
      if (!this.auth.username || !this.auth.password) { this.auth.error = "请填用户名和密码"; return; }
      if (this.auth.mode === "register" && !this.auth.email) { this.auth.error = "注册需要填写邮箱"; return; }
      if (!this.market.url) this.market.url = this.overview?.theme_market_url || "https://shop.swellengine.com/";
      this.auth.busy = true;
      this.auth.error = "";
      this.market.user = this.auth.username;
      this.market.pass = this.auth.password;
      await this.marketLogin(this.auth.mode);
      this.auth.busy = false;
      this.auth.password = "";
      if (this.market.token) {
        this.auth.show = false;
        await this.loadMy();
        if (this.tab === "market") await this.marketSearch();
      } else {
        this.auth.error = this.market.message || "登录失败";
      }
    },
    async restoreMarketSession() {
      if (this.market.token) return;
      try {
        const data = await apiPost("theme/market", { action: "session_get" });
        if (!data.token) return;
        this.market.token = data.token;
        this.market.user = data.username || "已登录";
        storageSet(THEME_TOKEN_KEY, this.market.token);
        storageSet(THEME_USER_KEY, this.market.user);
        if (this.tab === "market") await this.marketSearch();
        if (this.tab === "my") await this.loadMy();
      } catch (_) {}
    },
    async loadMy() {
      if (!this.market.token) { this.myItems = []; return; }
      try {
        const data = await apiPost("theme/market", { action: "my", url: this.market.url, token: this.market.token, kind: this.myKind });
        this.myItems = data.items || [];
        this.myLoaded = true;
      } catch (error) { this.toast(`读取失败：${error.message || error}`, "err"); }
    },
    async toggleLike(item) {
      if (!this.market.token) { this.toast("请先登录", "err"); return; }
      try {
        const data = await apiPost("theme/market", { action: "like", url: this.market.url, token: this.market.token, id: item.id });
        item.liked = data.liked;
        item.likes = data.likes;
      } catch (error) { this.toast(`操作失败：${error.message || error}`, "err"); }
    },
    async toggleFavorite(item) {
      if (!this.market.token) { this.toast("请先登录", "err"); return; }
      try {
        const data = await apiPost("theme/market", { action: "favorite", url: this.market.url, token: this.market.token, id: item.id });
        item.favorited = data.favorited;
      } catch (error) { this.toast(`操作失败：${error.message || error}`, "err"); }
    },
    async resolveConflict(mode) {
      const id = this.conflict.incoming?.id;
      if (!id || mode === "cancel") { this.conflict.show = false; return; }
      let name = String(this.conflict.name || "").trim();
      if (mode === "rename" && !name) { this.toast("请输入新的主题名称", "err"); return; }
      if (mode === "rename") {
        const existingName = String(this.conflict.existing?.name || "").trim();
        const incomingName = String(this.conflict.incoming?.name || "").trim();
        // 名字和已有主题/下载主题一样时自动加随机后缀，避免还是重名。
        if (name === existingName || name === incomingName) name = `${name}-${Math.random().toString(36).slice(2, 6)}`;
      }
      this.conflict.show = false;
      this.market.busy = true;
      try {
        this.startDownloadProgress();
        try {
          await apiPost("theme/market", {
            action: "download", url: this.market.url, token: this.market.token,
            id, mode, name: mode === "rename" ? name : "", apply: false,
          });
          await this.load();
          await this.loadMy();
          this.toast("已下载，请到「主题」页应用");
        } catch (error) {
          const msg = String(error.message || error);
          if (this.market.cancelling) {
            this.hideDownloadProgress();
            return;
          }
          this.market.download = { ...this.market.download, active: true, error: msg, phase: "下载失败" };
          this.toast(`下载失败：${msg}`, "err");
        } finally {
          this.stopDownloadProgress();
        }
      } catch (error) { this.toast(`下载失败：${error.message || error}`, "err"); }
      finally { this.market.busy = false; this.market.cancelling = false; }
    },
    async saveThemeMeta() {
      try {
        const data = await apiPost("theme", { action: "meta", ...this.themeForm });
        if (this.overview) this.overview.theme_meta = data.theme_meta;
        this.toast("主题信息已保存");
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async exportTheme() {
      this.busy = true;
      try {
        const data = await apiPost("theme", { action: "export", ...this.themeForm, include_videos: true });
        if (!data.url) throw new Error("公共目录不可用，无法下载");
        const link = document.createElement("a");
        link.href = data.url; link.download = data.filename || "theme.zip";
        document.body.appendChild(link); link.click(); link.remove();
        this.toast("主题已导出");
      } catch (error) { this.toast(`导出失败：${error.message || error}`, "err"); }
      finally { this.busy = false; }
    },
    async onThemeImport(event) {
      const file = event.target.files?.[0]; event.target.value = "";
      if (!file) return;
      this.busy = true;
      try {
        const result = await this.uploadFile(file, { theme: true });
        await this.load();
        if (await this.confirm("导入成功", `已导入「${result.theme?.name || result.theme_id}」，现在应用吗？`)) {
          await apiPost("theme", { action: "apply", id: result.theme_id });
          await this.load();
        }
        this.toast("主题已导入");
      } catch (error) { this.toast(`导入失败：${error.message || error}`, "err"); }
      finally { this.busy = false; }
    },
    async applyTheme(id) {
      const refresh = Boolean(this.overview) && id === this.overview.active_theme_id;
      if (refresh) {
        if (!(await this.confirm("刷新主题", "会用主题文件重新覆盖当前素材、画布和设置，确定吗？"))) return;
      } else if (!(await this.confirm("应用主题", "会覆盖当前素材、画布和设置，确定吗？"))) return;
      try {
        await apiPost("theme", { action: "apply", id });
        await this.load();
        this.toast(refresh ? "主题已刷新" : "主题已应用");
      } catch (error) { this.toast(`应用失败：${error.message || error}`, "err"); }
    },
    async deleteTheme(id) {
      if (!(await this.confirm("删除主题", "删除这个本地主题？不会删除已经播放的视频。", true))) return;
      try {
        const data = await apiPost("theme", { action: "delete", id });
        if (this.overview) this.overview.themes = data.themes || [];
        this.toast("主题已删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
    },
    async marketLogin(mode) {
      if (!this.market.url || !this.market.user || !this.market.pass) { this.toast("请填服务器、用户名和密码", "err"); return; }
      this.market.busy = true; this.market.message = "";
      try {
        const data = await apiPost("theme/market", {
          action: mode, url: this.market.url,
          username: this.market.user, password: this.market.pass, email: this.auth.email,
        });
        this.market.token = data.token; this.market.user = data.username;
        storageSet(THEME_TOKEN_KEY, data.token); storageSet(THEME_USER_KEY, data.username);
        storageSet(THEME_MARKET_KEY, this.market.url);
        apiPost("theme/market", {
          action: "session_save", url: this.market.url,
          token: data.token, username: data.username,
        }).catch(() => {});
        this.market.pass = "";
        this.toast(mode === "register" ? "注册成功" : "登录成功");
        await this.marketSearch();
      } catch (error) { this.market.message = error.message || String(error); this.toast(`登录失败：${this.market.message}`, "err"); }
      finally { this.market.busy = false; }
    },
    async marketSearch(reset = true) {
      if (!this.market.url) { this.market.message = "请先在设置里填写市场地址"; this.market.loaded = true; return; }
      if (reset === true) this.market.page = 1;
      this.market.busy = true; this.market.message = "";
      try {
        const data = await apiPost("theme/market", {
          action: "list", url: this.market.url, token: this.market.token, q: this.market.query,
          limit: this.market.pageSize,
          offset: (this.market.page - 1) * this.market.pageSize,
        });
        this.market.items = data.items || [];
        this.market.total = Number(data.total || 0);
        this.market.loaded = true;
        storageSet(THEME_MARKET_KEY, this.market.url);
      } catch (error) { this.market.message = error.message || String(error); this.market.loaded = true; }
      finally { this.market.busy = false; }
    },
    goMarketPage(page) {
      this.market.page = page;
      this.marketSearch(false);
    },
    async saveMarketUrl() {
      const url = String(this.market.url || "").trim().replace(/\/+$/, "");
      if (!/^https?:\/\//.test(url)) { this.toast("市场地址必须以 http:// 或 https:// 开头", "err"); return; }
      try {
        const data = await apiPost("settings", { theme_market_url: url });
        this.market.url = data.theme_market_url || url;
        if (this.overview) this.overview.theme_market_url = this.market.url;
        storageSet(THEME_MARKET_KEY, this.market.url);
        this.toast("市场地址已保存");
      } catch (error) {
        this.toast(`保存失败：${error.message || error}`, "err");
      }
    },
    downloadPercent() {
      const d = this.market.download || {};
      const total = Number(d.total || 0);
      if (total <= 0) return 0;
      return Math.max(0, Math.min(100, Math.round((Number(d.received || 0) / total) * 100)));
    },
    startDownloadProgress() {
      // 这里不能调用 stopDownloadProgress：它会安排一个 650ms 后隐藏的
      // 定时器，结果新下载刚亮起的弹窗会被上一个定时器关掉。
      if (this.market.download.poll) clearTimeout(this.market.download.poll);
      if (this.market.download.hideTimer) clearTimeout(this.market.download.hideTimer);
      this.market.cancelling = false;
      this.market.download = { active: true, phase: "准备下载", received: 0, total: 0, error: "", startedAt: Date.now(), hideTimer: 0 };
      const tick = async () => {
        if (!this.market.download.active || this.market.cancelling) return;
        try {
          const data = await apiPost("theme/market", { action: "progress" });
          if (this.market.cancelling) return;
          const p = data.progress || {};
          // 下载请求刚发出时后端可能还没进入 active，不能用一个空的
          // inactive 进度把刚亮起来的前端进度条立刻关掉。
          // 只接受后端“正在下载”或“失败”。后端先结束进度、前端还在
          // 等安装/刷新列表时，不能把弹窗提前关掉。
          if (p.active || p.error) {
            // 后端报错时 active 会变成 false，这里保持弹窗，让错误文案能显示出来。
            this.market.download = { ...this.market.download, ...p, active: true };
          }
        } catch (_) {}
        if (this.market.download.active) this.market.download.poll = setTimeout(tick, 350);
      };
      tick();
    },
    stopDownloadProgress() {
      if (this.market.download.poll) clearTimeout(this.market.download.poll);
      if (this.market.download.hideTimer) clearTimeout(this.market.download.hideTimer);
      this.market.download.poll = 0;
      const elapsed = Date.now() - Number(this.market.download.startedAt || Date.now());
      // 失败时多留一会儿，让用户看清错误原因再关掉弹窗。
      const stay = this.market.download.error ? 2600 : 650;
      const wait = Math.max(0, stay - elapsed);
      this.market.download.hideTimer = setTimeout(() => {
        this.market.download.active = false;
      }, wait);
    },
    hideDownloadProgress() {
      if (this.market.download.poll) clearTimeout(this.market.download.poll);
      if (this.market.download.hideTimer) clearTimeout(this.market.download.hideTimer);
      this.market.download.poll = 0;
      this.market.download.hideTimer = 0;
      this.market.download.active = false;
    },
    async marketDownload(id) {
      // 下载不覆盖当前主题，不需要额外确认；第一次点击就直接开始。
      this.market.busy = true;
      // 先亮进度弹窗，点下去立刻有反馈；确认不冲突后再真正开始下载。
      this.startDownloadProgress();
      try {
        const ask = await apiPost("theme/market", { action: "download", url: this.market.url, token: this.market.token, id, mode: "ask" });
        if (ask.conflict) {
          this.hideDownloadProgress();
          this.conflict = { show: true, incoming: ask.incoming, existing: ask.existing, name: ask.incoming?.name || "" };
          return;
        }
        try {
          await apiPost("theme/market", { action: "download", url: this.market.url, token: this.market.token, id, apply: false });
          await this.load();
          this.toast("已下载，请到「主题」页应用");
        } catch (error) {
          const msg = String(error.message || error);
          if (this.market.cancelling) {
            this.hideDownloadProgress();
            return;
          }
          // 让弹窗停在“下载失败 + 原因”，而不是一闪而过。
          this.market.download = { ...this.market.download, active: true, error: msg, phase: "下载失败" };
          this.toast(`下载失败：${msg}`, "err");
        } finally {
          this.stopDownloadProgress();
        }
      } catch (error) { this.toast(`下载失败：${error.message || error}`, "err"); }
      finally { this.market.busy = false; this.market.cancelling = false; }
    },
    async cancelDownload() {
      // 立刻收起弹窗，别让遮罩挡住面板；后端会在下一个分块中断开连接。
      this.market.cancelling = true;
      this.hideDownloadProgress();
      this.toast("已取消下载");
      try { await apiPost("theme/market", { action: "cancel_download" }); } catch (_) {}
    },
    async openUploadDialog(targetId = "") {
      if (!this.market.token) { this.toast("请先登录市场", "err"); return; }
      this.uploadDialog.projectId = this.projectId || this.overview?.active_theme_id || this.overview?.themes?.[0]?.id || "";
      this.uploadDialog.targetId = targetId || "new";
      this.uploadDialog.busy = false;
      this.uploadDialog.show = true;
      try {
        const data = await apiPost("theme/market", {
          action: "my", url: this.market.url, token: this.market.token, kind: "published",
        });
        this.myPublished = data.items || [];
      } catch (_) {
        this.myPublished = (this.market.items || []).filter((item) => item.mine);
      }
    },
    async marketUpload() {
      if (!this.market.token) { this.toast("请先登录市场", "err"); return; }
      if (!this.uploadDialog.projectId) { this.toast("请选择要上传的项目", "err"); return; }
      this.uploadDialog.busy = true;
      const updating = Boolean(this.uploadDialog.targetId) && this.uploadDialog.targetId !== "new";
      try {
        const payload = {
          url: this.market.url,
          token: this.market.token,
          project_id: this.uploadDialog.projectId,
        };
        let data;
        if (updating) {
          payload.action = "update_market";
          payload.id = this.uploadDialog.targetId;
          data = await apiPost("theme/market", payload);
        } else {
          payload.action = "upload";
          data = await apiPost("theme/market", payload);
        }
        this.uploadDialog.show = false;
        this.toast(`${updating ? "更新" : "上传"}成功：${data.item?.name || "已提交"}`);
        await this.marketSearch();
      } catch (error) { this.toast(`上传失败：${error.message || error}`, "err"); }
      finally { this.uploadDialog.busy = false; }
    },
    openMarketEdit(item) {
      this.editDialog = {
        show: true, id: item.id, name: item.name || "",
        description: item.description || "", version: item.version || "1.0.0", busy: false,
      };
    },
    async saveMarketInfo() {
      if (!this.editDialog.name.trim()) { this.toast("主题名不能为空", "err"); return; }
      this.editDialog.busy = true;
      try {
        const data = await apiPost("theme/market", {
          action: "update_info", url: this.market.url, token: this.market.token,
          id: this.editDialog.id, name: this.editDialog.name,
          description: this.editDialog.description, version: this.editDialog.version,
        });
        const item = data.item || {};
        for (const list of [this.market.items, this.myItems, this.myPublished]) {
          const found = (list || []).find((x) => x.id === this.editDialog.id);
          if (found) Object.assign(found, item);
        }
        this.editDialog.show = false;
        this.toast("市场信息已更新");
      } catch (error) { this.toast(`更新失败：${error.message || error}`, "err"); }
      finally { this.editDialog.busy = false; }
    },
    async marketDelete(id) {
      if (!(await this.confirm("从市场删除", "删除你在市场上传的这个主题？", true))) return;
      try {
        await apiPost("theme/market", { action: "delete_market", url: this.market.url, token: this.market.token, id });
        await this.marketSearch();
        this.toast("已从市场删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
    },
    async toggleBubble() {
      try {
        const data = await apiPost("settings", {
          bubble_enabled: Boolean(this.settingsForm.bubble_enabled),
        });
        if (this.overview) this.overview.settings = data.settings || this.overview.settings;
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async saveSettings() {
      try {
        const data = await apiPost("settings", {
          crossfade_ms: Number(this.settingsForm.crossfade_ms || 0),
          bubble_enabled: Boolean(this.settingsForm.bubble_enabled),
        });
        if (this.overview) this.overview.settings = data.settings || this.overview.settings;
        this.toast("播放设置已保存");
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async loadBackups() {
      try {
        const data = await apiGet("backups");
        this.backups = (data.backups || []).map((item) => ({
          id: item.id,
          label: `${item.created_at ? new Date(item.created_at * 1000).toLocaleString() : item.id} · ${item.reason || "自动"} · ${(item.videos || []).length} 个视频`,
        }));
      } catch (_) { this.backups = []; }
    },
    async restoreBackup() {
      if (!this.backupId) { this.toast("请选择备份", "err"); return; }
      if (!(await this.confirm("恢复备份", "会用备份覆盖当前配置和视频，确定吗？", true))) return;
      try { await apiPost("restore", { id: this.backupId }); await this.load(); this.toast("备份已恢复"); }
      catch (error) { this.toast(`恢复失败：${error.message || error}`, "err"); }
    },
    async sendBubble() {
      if (!this.bubbleText.trim()) return;
      try {
        const data = await apiPost("event", { action: "bubble", text: this.bubbleText.trim() });
        this.state = data.state || this.state; this.toast("气泡已发送");
      } catch (error) { this.toast(`发送失败：${error.message || error}`, "err"); }
    },
    async clearBubble() {
      try { const data = await apiPost("event", { action: "bubble", text: "" }); this.state = data.state || this.state; this.bubbleText = ""; }
      catch (error) { this.toast(`清空失败：${error.message || error}`, "err"); }
    },
  },
};
</script>
