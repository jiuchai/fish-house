const { createApp } = Vue;

const THEME_TOKEN_KEY = "fishHouseThemeToken";
const THEME_MARKET_KEY = "fishHouseThemeMarket";
const THEME_USER_KEY = "fishHouseThemeUser";
const memoryStore = {};

function storageGet(key) {
  try { return window.localStorage.getItem(key); }
  catch (_) { return Object.prototype.hasOwnProperty.call(memoryStore, key) ? memoryStore[key] : null; }
}
function storageSet(key, value) {
  try { window.localStorage.setItem(key, String(value)); }
  catch (_) { memoryStore[key] = String(value); }
}

const inIframe = window.self !== window.top;
const API_MARKER = "/api/v1/plugins/";
const markerIndex = window.location.pathname.indexOf(API_MARKER);
const APP_PREFIX = markerIndex >= 0 ? window.location.pathname.slice(0, markerIndex) : "";
const API_BASE = `${APP_PREFIX}/api/v1/plugins/extensions/astrbot_plugin_fish_house`;
const PUBLIC_FALLBACK = `${APP_PREFIX}/fish_house/index.html`;

function bridgeView() {
  return inIframe ? window.AstrBotPluginView || window.AstrBotPluginPage || null : null;
}
async function bridgeRequest(path, params, body, method) {
  const view = bridgeView();
  if (!view) throw new Error("插件页桥接还没有准备好");
  const data = method === "GET"
    ? await view.apiGet(path, params || {})
    : await view.apiPost(path, body || {});
  if (data && data.status === "error") throw new Error(data.message || "接口返回错误");
  return data || {};
}
async function cookieRequest(path, params, body, method) {
  const url = new URL(`${API_BASE}/${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method, credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined, cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.status === "error") throw new Error(data.message || `HTTP ${response.status}`);
  return data;
}
async function apiRequest(path, params, body, method) {
  // Vue 的响应式对象是 Proxy，直接 postMessage 会报 “could not be cloned”。
  // 统一先做一次 JSON 深拷贝，既去掉 Proxy，也去掉 undefined/函数。
  const safeBody = body == null ? body : JSON.parse(JSON.stringify(body));
  // 有 bridge 时只走 bridge：回退到 cookie fetch 会在 sandbox 里变成 “Failed to fetch”，
  // 把后端真实错误（例如用户名已存在）掩盖掉。
  if (bridgeView()) return bridgeRequest(path, params, safeBody, method);
  return cookieRequest(path, params, safeBody, method);
}
const apiGet = (path, params) => apiRequest(path, params, null, "GET");
const apiPost = (path, body) => apiRequest(path, null, body || {}, "POST");

function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

const TABS = [
  { key: "overview", label: "首页", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/></svg>' },
  { key: "themes", label: "主题", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>' },
  { key: "project", label: "项目", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="m8.3 7 7.4.4M7.4 8.3l3.3 7.3M16.6 9.2l-3.2 6.4"/></svg>' },
  { key: "market", label: "市场", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M7 18a4 4 0 0 1 0-8 6 6 0 0 1 11.5 1.5A3.5 3.5 0 0 1 18 18H7Z"/><path d="M12 10v5m0-5-2 2m2-2 2 2"/></svg>' },
  { key: "my", label: "我的", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>' },
  { key: "settings", label: "设置", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-5l-.4 2.6a7 7 0 0 0-1.7 1l-2.4-1-2 3.4L5 11.5a7 7 0 0 0 0 1L3 14l2 3.4 2.4-1a7 7 0 0 0 1.7 1l.4 2.6h5l.4-2.6a7 7 0 0 0 1.7-1l2.4 1 2-3.4-2-1.5c.1-.3.1-.7.1-1Z"/></svg>' },
];

const ROLE_LABELS = { wait: "循环等待", both: "回消息/工作", work: "仅工作", phone: "仅回消息", rest: "循环等待", action: "动作", transition: "过渡" };
const MODE_LABELS = { rest: "休息", phone: "手机", computer: "电脑", action: "动作", flow: "画布", outfit_change: "换装" };

const app = createApp({
  data() {
    return {
      loading: true, error: "", tab: "overview", busy: false,
      overview: null, state: { mode: "rest", bubble: "" },
      libraryQuery: "", preview: null,
      toastData: { show: false, text: "", type: "" },
      dialog: { show: false, title: "", text: "", mode: "confirm", value: "", danger: false },
      settingsForm: { crossfade_ms: 400, auto_reply: true },
      themeForm: { name: "我的主题", author: "", version: "1.0.0", description: "" },
      market: { url: "", user: "", pass: "", token: "", query: "", items: [], loaded: false, busy: false, message: "", page: 1, pageSize: 12, total: 0 },
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
      const s = this.state || {};
      if (s.mode === "flow") return nodes.find((n) => n.id === s.node_id) || null;
      if (s.node_id) return nodes.find((n) => n.id === s.node_id) || null;
      if (s.mode === "rest") return nodes.find((n) => n.role === "rest") || nodes.find((n) => n.role === "wait") || null;
      const role = s.mode === "computer" ? "work" : s.mode === "phone" ? "phone" : "";
      return role ? nodes.find((n) => n.role === role) || null : null;
    },
  },
  async mounted() {
    await this.load();
    this.poller = setInterval(() => this.pollState(), 1000);
    this.loadBackups();
    window.addEventListener("wheel", this.onShellWheel, { passive: false });
  },
  beforeUnmount() {
    clearInterval(this.poller);
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
          auto_reply: Boolean(Number(data.settings?.auto_reply ?? 1)),
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
        this.state = data.state || this.state;
        if (this.overview) this.overview.state = this.state;
      } catch (_) {}
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
      link.download = filename || "theme.fishtheme";
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
    async onSlotUpload(slot, event) {
      const file = event.target.files?.[0]; event.target.value = "";
      if (!file) return;
      try {
        await this.uploadFile(file, { slot: slot.key });
        await this.load();
        this.toast(`${slot.label} 已更新`);
      } catch (error) { this.toast(`上传失败：${error.message || error}`, "err"); }
    },
    async setSlotUrl(slot) {
      const url = await this.prompt(`设置「${slot.label}」视频地址`, "填 http(s) 链接或 videos/xxx.mp4", slot.url || "");
      if (!url) return;
      try { await apiPost("slot", { slot: slot.key, url }); await this.load(); this.toast("地址已保存"); }
      catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
    },
    async clearSlot(slot) {
      if (!(await this.confirm("清除槽位", `确定清除「${slot.label}」的视频吗？`, true))) return;
      try { await apiPost("slot", { slot: slot.key, url: "" }); await this.load(); this.toast("已清除"); }
      catch (error) { this.toast(`清除失败：${error.message || error}`, "err"); }
    },
    async toggleAudio(slot, event) {
      try {
        const data = await apiPost("settings", { audio: { [slot.key]: event.target.checked } });
        if (this.overview) this.overview.settings = data.settings || this.overview.settings;
      } catch (error) { this.toast(`保存失败：${error.message || error}`, "err"); }
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
    async deleteMedia(item) {
      if (!(await this.confirm("删除素材", `从素材库删除「${item.name}」？被节点引用时会拒绝。`, true))) return;
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
      if (!(await this.confirm("删除素材", `删除项目素材「${item.name}」？被节点引用时会拒绝。`, true))) return;
      try {
        const data = await apiPost("theme/project", { action: "asset_delete", id: this.projectId, media_id: item.id });
        this.project = data.project;
        this.toast("素材已删除");
      } catch (error) { this.toast(`删除失败：${error.message || error}`, "err"); }
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
          this.downloadUrl(data.url, data.filename || `${this.projectId}.fishtheme`);
          this.toast("项目已导出");
        }
        else this.toast("导出成功，但公共目录不可用", "err");
      } catch (error) { this.toast(`导出失败：${error.message || error}`, "err"); }
    },
    async applyProject() {
      if (!this.project) return;
      if (!(await this.confirm("应用项目", "把当前项目应用为桌宠配置？会覆盖当前素材和画布。", true))) return;
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
      this.conflict.show = false;
      if (!id || mode === "cancel") return;
      this.market.busy = true;
      try {
        await apiPost("theme/market", { action: "download", url: this.market.url, token: this.market.token, id, mode, apply: true });
        await this.load();
        await this.loadMy();
        this.toast(mode === "overwrite" ? "已覆盖并应用" : "已重命名并应用");
      } catch (error) { this.toast(`下载失败：${error.message || error}`, "err"); }
      finally { this.market.busy = false; }
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
        link.href = data.url; link.download = data.filename || "theme.fishtheme";
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
      if (!(await this.confirm("应用主题", "会覆盖当前素材、动作、画布和设置，确定吗？"))) return;
      try { await apiPost("theme", { action: "apply", id }); await this.load(); this.toast("主题已应用"); }
      catch (error) { this.toast(`应用失败：${error.message || error}`, "err"); }
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
    async marketDownload(id) {
      if (!(await this.confirm("下载主题", "下载后会应用为当前配置，确定吗？"))) return;
      this.market.busy = true;
      try {
        const ask = await apiPost("theme/market", { action: "download", url: this.market.url, token: this.market.token, id, mode: "ask" });
        if (ask.conflict) {
          this.conflict = { show: true, incoming: ask.incoming, existing: ask.existing };
          return;
        }
        await apiPost("theme/market", { action: "download", url: this.market.url, token: this.market.token, id, apply: true });
        await this.load();
        this.toast("主题已下载并应用");
      } catch (error) { this.toast(`下载失败：${error.message || error}`, "err"); }
      finally { this.market.busy = false; }
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
    async saveSettings() {
      try {
        const data = await apiPost("settings", {
          crossfade_ms: Number(this.settingsForm.crossfade_ms || 0),
          auto_reply: Boolean(this.settingsForm.auto_reply),
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
});
app.use(ElementPlus);
app.component("theme-card", window.ThemeCard);
app.component("canvas-panel", window.CanvasPanel);
app.mount("#app");
