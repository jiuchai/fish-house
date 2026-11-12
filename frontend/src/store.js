import { computed, reactive } from "vue";
import {
  apiGet, apiPost, bufferToBase64, storageGet, storageSet,
  THEME_TOKEN_KEY, THEME_MARKET_KEY, THEME_USER_KEY,
} from "./api.js";
import { MODE_LABELS } from "./constants.js";

export const DEFAULT_MARKET_URL = "https://shop.swellengine.com/";

// 面板的共享状态：各个 tab 与弹窗组件都直接读写这里，避免层层透传 props。
export const panel = reactive({
  loading: true,
  error: "",
  tab: "overview",
  overview: null,
  live: { mode: "flow", bubble: "" },
  preview: null,
  coverPreview: "",
  toastData: { show: false, text: "", type: "" },
  dialog: { show: false, title: "", text: "", mode: "confirm", value: "", danger: false },
  settingsForm: { crossfade_ms: 400, bubble_enabled: true },
  market: {
    url: "", user: "", pass: "", token: "", query: "", items: [], loaded: false,
    busy: false, cancelling: false, message: "", page: 1, pageSize: 12, total: 0,
    download: { active: false, phase: "", received: 0, total: 0, error: "" },
  },
  bubbleText: "",
  projectId: "",
  project: null,
  projectBase: "",
  projectForm: { name: "", author: "", version: "1.0.0", description: "" },
  auth: { mode: "login", username: "", password: "", email: "", busy: false, show: false, error: "" },
  myKind: "published",
  myItems: [],
  uploadDialog: { show: false, projectId: "", targetId: "", busy: false },
  editDialog: { show: false, id: "", name: "", description: "", version: "", busy: false },
  myPublished: [],
  conflict: { show: false, incoming: null, existing: null, name: "" },
});

export const activeTheme = computed(() => {
  const themes = panel.overview?.themes || [];
  return themes.find((item) => item.id === panel.overview?.active_theme_id) || themes[0] || null;
});

export const heroStyle = computed(() => {
  const cover = activeTheme.value?.cover_url;
  return cover ? { backgroundImage: `url("${cover}")` } : {};
});

export const publicUrl = computed(() => {
  if (panel.overview?.public_url) return panel.overview.public_url;
  return `${window.location.origin}/${panel.overview?.prefix || "fish_house"}/index.html`;
});

export const projectAssets = computed(() => panel.project?.theme?.config?.library || []);

export const projectFlow = computed(() => panel.project?.theme?.config?.flow || { nodes: [], edges: [] });

export function modeLabel(mode) {
  return MODE_LABELS[mode] || mode || "休息";
}

export function fmtDuration(value) {
  const n = Number(value || 0);
  return n > 0 ? `${n.toFixed(1)}s` : "时长未知";
}

export function mediaUrl(value) {
  const url = String(value || "");
  if (!url) return "";
  if (/^(https?:|data:|blob:)/.test(url) || url.startsWith("/")) return url;
  return `/${panel.overview?.prefix || "fish_house"}/${url.replace(/^\.?\//, "")}`;
}

export function projectMediaUrl(video) {
  const url = String(video || "");
  if (!url) return "";
  if (/^(https?:|data:|blob:)/.test(url) || url.startsWith("/")) return url;
  return `${panel.projectBase}/${url.replace(/^\.?\//, "")}`;
}

export function toast(text, type = "") {
  panel.toastData = { show: true, text, type };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { panel.toastData.show = false; }, 2600);
}
let toastTimer = 0;

let dialogResolve = null;
export function ask(title, text, options = {}) {
  return new Promise((resolve) => {
    panel.dialog = {
      show: true, title, text,
      mode: options.mode || "confirm",
      value: options.value || "",
      danger: Boolean(options.danger),
    };
    dialogResolve = resolve;
  });
}
export function confirm(title, text, danger = false) { return ask(title, text, { danger }); }
export function prompt(title, text, value = "") { return ask(title, text, { mode: "prompt", value }); }
export function dialogOk() {
  const value = panel.dialog.mode === "prompt" ? panel.dialog.value : true;
  panel.dialog.show = false;
  const resolve = dialogResolve;
  dialogResolve = null;
  if (resolve) resolve(value);
}
export function dialogCancel() {
  panel.dialog.show = false;
  const resolve = dialogResolve;
  dialogResolve = null;
  if (resolve) resolve(panel.dialog.mode === "prompt" ? null : false);
}

export async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("已复制");
}

function downloadUrl(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "theme.zip";
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function uploadFile(file, extra) {
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
}

function nodesUsing(nodes, mediaId) {
  return (nodes || [])
    .filter((node) => String(node.media_id || "") === String(mediaId))
    .map((node) => node.label || node.id);
}

/* ---------------- 概览 / 实时状态 ---------------- */

export async function load() {
  panel.loading = true;
  try {
    const data = await apiGet("overview");
    panel.overview = data;
    panel.live = data.state || {};
    panel.settingsForm = {
      crossfade_ms: Number(data.settings?.crossfade_ms ?? 400),
      bubble_enabled: Number(data.settings?.bubble_enabled ?? 1) !== 0,
    };
    if (!panel.market.url) {
      panel.market.url = data.theme_market_url || storageGet(THEME_MARKET_KEY) || DEFAULT_MARKET_URL;
    }
    panel.market.token = storageGet(THEME_TOKEN_KEY) || "";
    panel.market.user = storageGet(THEME_USER_KEY) || "";
    panel.error = "";
    if (!panel.market.token) restoreMarketSession();
  } catch (error) {
    panel.error = error.message || String(error);
  } finally {
    panel.loading = false;
  }
}

function applyState(state) {
  if (!state) return;
  panel.live = state;
  if (panel.overview) panel.overview.state = state;
}

let poller = null;

// 面板不再另开 WebSocket，和壁纸页一样按固定间隔拉 state。
export function connectLive() {
  if (poller) return;
  const tick = async () => {
    try {
      const data = await apiGet("state");
      applyState(data.state);
    } catch (_) {}
  };
  void tick();
  poller = setInterval(tick, 1000);
}

export function closeLive() {
  if (!poller) return;
  clearInterval(poller);
  poller = null;
}

/* ---------------- 主题 ---------------- */

export async function onThemeImport(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try {
    const result = await uploadFile(file, { theme: true });
    await load();
    if (await confirm("导入成功", `已导入「${result.theme?.name || result.theme_id}」，现在应用吗？`)) {
      await apiPost("theme", { action: "apply", id: result.theme_id });
      await load();
    }
    toast("主题已导入");
  } catch (error) {
    toast(`导入失败：${error.message || error}`, "err");
  }
}

export async function applyTheme(id) {
  const refresh = Boolean(panel.overview) && id === panel.overview.active_theme_id;
  if (refresh) {
    if (!(await confirm("刷新主题", "会用主题文件重新覆盖当前素材、画布和设置，确定吗？"))) return;
  } else if (!(await confirm("应用主题", "会覆盖当前素材、画布和设置，确定吗？"))) return;
  try {
    await apiPost("theme", { action: "apply", id });
    await load();
    toast(refresh ? "主题已刷新" : "主题已应用");
  } catch (error) {
    toast(`应用失败：${error.message || error}`, "err");
  }
}

export async function deleteTheme(id) {
  if (!(await confirm("删除主题", "删除这个本地主题？不会删除已经播放的视频。", true))) return;
  try {
    const data = await apiPost("theme", { action: "delete", id });
    if (panel.overview) panel.overview.themes = data.themes || [];
    toast("主题已删除");
  } catch (error) {
    toast(`删除失败：${error.message || error}`, "err");
  }
}

export function openThemeProject(id) {
  panel.tab = "project";
  loadProject(id);
}

/* ---------------- 项目 ---------------- */

export async function loadProject(id) {
  if (!id) return;
  try {
    const data = await apiPost("theme/project", { action: "load", id });
    panel.projectId = id;
    panel.project = data.project;
    panel.projectBase = data.project.public_base || "";
    const meta = data.project.theme.meta || {};
    panel.projectForm = {
      name: meta.name || "", author: meta.author || "",
      version: meta.version || "1.0.0", description: meta.description || "",
    };
  } catch (error) {
    toast(`读取项目失败：${error.message || error}`, "err");
  }
}

export async function createProject(isCopy) {
  const name = await prompt(
    isCopy ? "复制项目" : "新建项目",
    "给新主题起个名字",
    isCopy && panel.project ? `${panel.projectForm.name} 副本` : "新主题",
  );
  if (!name) return;
  try {
    const data = await apiPost("theme/project", isCopy
      ? { action: "duplicate", id: panel.projectId, name }
      : { action: "create", name, author: panel.overview?.theme_meta?.author || "" });
    await load();
    await loadProject(data.project.id);
    toast(isCopy ? "项目已复制" : "项目已创建");
  } catch (error) {
    toast(`创建失败：${error.message || error}`, "err");
  }
}

export async function saveProjectMeta() {
  if (!panel.project) return;
  try {
    const data = await apiPost("theme/project", {
      action: "save", id: panel.projectId,
      meta: panel.projectForm,
      config: panel.project.theme.config,
    });
    panel.project = data.project;
    await load();
    toast("项目已保存");
  } catch (error) {
    toast(`保存失败：${error.message || error}`, "err");
  }
}

export async function saveProjectFlow(flow) {
  if (!panel.project) return;
  panel.project.theme.config.flow = flow;
  try {
    const data = await apiPost("theme/project", {
      action: "save", id: panel.projectId,
      meta: panel.projectForm,
      config: panel.project.theme.config,
    });
    panel.project = data.project;
    toast("画布已保存到项目");
  } catch (error) {
    toast(`保存失败：${error.message || error}`, "err");
  }
}

export async function onProjectAsset(event) {
  const files = [...(event.target.files || [])];
  event.target.value = "";
  if (!panel.project) return;
  for (const file of files) {
    try {
      const data = await uploadFile(file, { target: "project_asset", project_id: panel.projectId });
      panel.project = data.project;
      panel.projectBase = data.project.public_base || panel.projectBase;
      toast(`素材已加入：${file.name}`);
    } catch (error) {
      toast(`上传失败：${error.message || error}`, "err");
    }
  }
}

export async function deleteProjectAsset(item) {
  const used = nodesUsing(panel.project?.theme?.config?.flow?.nodes, item.id);
  if (used.length) {
    toast(`素材「${item.name}」还被节点使用：${used.join("、")}，不能删除`, "err");
    return;
  }
  if (!(await confirm("删除素材", `删除项目素材「${item.name}」？`, true))) return;
  try {
    const data = await apiPost("theme/project", { action: "asset_delete", id: panel.projectId, media_id: item.id });
    panel.project = data.project;
    toast("素材已删除");
  } catch (error) {
    toast(`删除失败：${error.message || error}`, "err");
  }
}

export async function renameProjectAsset(item) {
  if (!panel.project) return;
  let name = await prompt("素材改名", "输入新的素材名", item.name);
  if (!name) return;
  // 名字没改时加随机后缀，避免和原来的名字完全一样。
  if (name === item.name) name = `${name}-${Math.random().toString(36).slice(2, 6)}`;
  try {
    const data = await apiPost("theme/project", { action: "asset_rename", id: panel.projectId, media_id: item.id, name });
    panel.project = data.project;
    toast("素材已改名");
  } catch (error) {
    toast(`改名失败：${error.message || error}`, "err");
  }
}

export async function replaceProjectAsset(item, event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !panel.project) return;
  try {
    const data = await uploadFile(file, { target: "project_asset", project_id: panel.projectId, replace_id: item.id });
    panel.project = data.project;
    toast(`已更换「${item.name}」的视频`);
  } catch (error) {
    toast(`更换失败：${error.message || error}`, "err");
  }
}

export async function downloadProjectAsset(item) {
  const url = projectMediaUrl(item.video);
  if (!url) return;
  const ext = ((url.match(/\.(mp4|webm|m4v|mov)(?:\?|$)/i) || [".mp4"])[0]).replace(/\?.*$/, "");
  const filename = `${item.name || "素材"}${ext}`;
  // 插件页在 sandbox iframe 里，直接 fetch 公共目录会被跨源拦住，交给父页面走插件接口下载。
  const view = window.AstrBotPluginView || window.AstrBotPluginPage;
  const name = url.split("?")[0].split("/").pop();
  if (view && typeof view.download === "function" && name) {
    await view.download("media", { name }, filename);
    return;
  }
  try {
    // 用 blob 下载，避免跨源时 download 属性失效变成直接打开视频。
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 4000);
    toast(`已下载：${item.name || "素材"}`);
  } catch (error) {
    window.open(url, "_blank");
    toast(`浏览器未能直接下载，已在新窗口打开：${error.message || error}`, "err");
  }
}

export async function onProjectCover(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !panel.project) return;
  try {
    const data = await uploadFile(file, { target: "theme_cover", project_id: panel.projectId });
    panel.project = data.project;
    toast("封面已更新");
  } catch (error) {
    toast(`封面上传失败：${error.message || error}`, "err");
  }
}

export async function exportProject() {
  if (!panel.project) return;
  try {
    const data = await apiPost("theme/project", { action: "export", id: panel.projectId });
    if (data.url) {
      downloadUrl(data.url, data.filename || `${panel.projectId}.zip`);
      toast("项目已导出");
    } else {
      toast("导出成功，但公共目录不可用", "err");
    }
  } catch (error) {
    toast(`导出失败：${error.message || error}`, "err");
  }
}

export async function applyProject() {
  if (!panel.project) return;
  if (!(await confirm("应用项目", "把当前项目应用为壁纸？会覆盖当前素材和画布。", true))) return;
  try {
    await apiPost("theme", { action: "apply", id: panel.projectId });
    await load();
    toast("项目已应用");
  } catch (error) {
    toast(`应用失败：${error.message || error}`, "err");
  }
}

export async function deleteProject() {
  if (!panel.project) return;
  if (!(await confirm("删除项目", `删除项目「${panel.projectForm.name}」？视频文件会一起删除。`, true))) return;
  try {
    await apiPost("theme/project", { action: "delete", id: panel.projectId });
    panel.project = null;
    panel.projectId = "";
    await load();
    toast("项目已删除");
  } catch (error) {
    toast(`删除失败：${error.message || error}`, "err");
  }
}

/* ---------------- 账号 ---------------- */

export function openAuth(mode = "login") {
  panel.auth.mode = mode;
  panel.auth.error = "";
  panel.auth.show = true;
}

export function logout() {
  apiPost("theme/market", { action: "session_clear" }).catch(() => {});
  panel.market.token = "";
  panel.market.user = "";
  panel.myItems = [];
  storageSet(THEME_TOKEN_KEY, "");
  panel.auth.show = false;
}

export async function submitAuth() {
  if (!panel.auth.username || !panel.auth.password) { panel.auth.error = "请填用户名和密码"; return; }
  if (panel.auth.mode === "register" && !panel.auth.email) { panel.auth.error = "注册需要填写邮箱"; return; }
  if (!panel.market.url) panel.market.url = panel.overview?.theme_market_url || DEFAULT_MARKET_URL;
  panel.auth.busy = true;
  panel.auth.error = "";
  panel.market.user = panel.auth.username;
  panel.market.pass = panel.auth.password;
  await marketLogin(panel.auth.mode);
  panel.auth.busy = false;
  panel.auth.password = "";
  if (panel.market.token) {
    panel.auth.show = false;
    await loadMy();
    if (panel.tab === "market") await marketSearch();
  } else {
    panel.auth.error = panel.market.message || "登录失败";
  }
}

async function restoreMarketSession() {
  if (panel.market.token) return;
  try {
    const data = await apiPost("theme/market", { action: "session_get" });
    if (!data.token) return;
    panel.market.token = data.token;
    panel.market.user = data.username || "已登录";
    storageSet(THEME_TOKEN_KEY, panel.market.token);
    storageSet(THEME_USER_KEY, panel.market.user);
    if (panel.tab === "market") await marketSearch();
    if (panel.tab === "my") await loadMy();
  } catch (_) {}
}

async function marketLogin(mode) {
  if (!panel.market.url || !panel.market.user || !panel.market.pass) { toast("请填服务器、用户名和密码", "err"); return; }
  panel.market.busy = true;
  panel.market.message = "";
  try {
    const data = await apiPost("theme/market", {
      action: mode, url: panel.market.url,
      username: panel.market.user, password: panel.market.pass, email: panel.auth.email,
    });
    panel.market.token = data.token;
    panel.market.user = data.username;
    storageSet(THEME_TOKEN_KEY, data.token);
    storageSet(THEME_USER_KEY, data.username);
    storageSet(THEME_MARKET_KEY, panel.market.url);
    apiPost("theme/market", {
      action: "session_save", url: panel.market.url,
      token: data.token, username: data.username,
    }).catch(() => {});
    panel.market.pass = "";
    toast(mode === "register" ? "注册成功" : "登录成功");
    await marketSearch();
  } catch (error) {
    panel.market.message = error.message || String(error);
    toast(`登录失败：${panel.market.message}`, "err");
  } finally {
    panel.market.busy = false;
  }
}

export async function loadMy() {
  if (!panel.market.token) { panel.myItems = []; return; }
  try {
    const data = await apiPost("theme/market", {
      action: "my", url: panel.market.url, token: panel.market.token, kind: panel.myKind,
    });
    panel.myItems = data.items || [];
  } catch (error) {
    toast(`读取失败：${error.message || error}`, "err");
  }
}

export async function toggleLike(item) {
  if (!panel.market.token) { toast("请先登录", "err"); return; }
  try {
    const data = await apiPost("theme/market", { action: "like", url: panel.market.url, token: panel.market.token, id: item.id });
    item.liked = data.liked;
    item.likes = data.likes;
  } catch (error) {
    toast(`操作失败：${error.message || error}`, "err");
  }
}

export async function toggleFavorite(item) {
  if (!panel.market.token) { toast("请先登录", "err"); return; }
  try {
    const data = await apiPost("theme/market", { action: "favorite", url: panel.market.url, token: panel.market.token, id: item.id });
    item.favorited = data.favorited;
  } catch (error) {
    toast(`操作失败：${error.message || error}`, "err");
  }
}

/* ---------------- 市场 ---------------- */

export async function marketSearch(reset = true) {
  if (!panel.market.url) { panel.market.message = "请先在设置里填写市场地址"; panel.market.loaded = true; return; }
  if (reset === true) panel.market.page = 1;
  panel.market.busy = true;
  panel.market.message = "";
  try {
    const data = await apiPost("theme/market", {
      action: "list", url: panel.market.url, token: panel.market.token, q: panel.market.query,
      limit: panel.market.pageSize,
      offset: (panel.market.page - 1) * panel.market.pageSize,
    });
    panel.market.items = data.items || [];
    panel.market.total = Number(data.total || 0);
    panel.market.loaded = true;
    storageSet(THEME_MARKET_KEY, panel.market.url);
  } catch (error) {
    panel.market.message = error.message || String(error);
    panel.market.loaded = true;
  } finally {
    panel.market.busy = false;
  }
}

export function goMarketPage(page) {
  panel.market.page = page;
  marketSearch(false);
}

export async function saveMarketUrl() {
  const url = String(panel.market.url || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//.test(url)) { toast("市场地址必须以 http:// 或 https:// 开头", "err"); return; }
  try {
    const data = await apiPost("settings", { theme_market_url: url });
    panel.market.url = data.theme_market_url || url;
    if (panel.overview) panel.overview.theme_market_url = panel.market.url;
    storageSet(THEME_MARKET_KEY, panel.market.url);
    toast("市场地址已保存");
  } catch (error) {
    toast(`保存失败：${error.message || error}`, "err");
  }
}

export function downloadPercent() {
  const d = panel.market.download || {};
  const total = Number(d.total || 0);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((Number(d.received || 0) / total) * 100)));
}

function startDownloadProgress() {
  // 这里不能调用 stopDownloadProgress：它会安排一个 650ms 后隐藏的
  // 定时器，结果新下载刚亮起的弹窗会被上一个定时器关掉。
  if (panel.market.download.poll) clearTimeout(panel.market.download.poll);
  if (panel.market.download.hideTimer) clearTimeout(panel.market.download.hideTimer);
  panel.market.cancelling = false;
  panel.market.download = { active: true, phase: "准备下载", received: 0, total: 0, error: "", startedAt: Date.now(), hideTimer: 0 };
  const tick = async () => {
    if (!panel.market.download.active || panel.market.cancelling) return;
    try {
      const data = await apiPost("theme/market", { action: "progress" });
      if (panel.market.cancelling) return;
      const p = data.progress || {};
      // 下载请求刚发出时后端可能还没进入 active，不能用一个空的
      // inactive 进度把刚亮起来的前端进度条立刻关掉。
      // 只接受后端“正在下载”或“失败”。后端先结束进度、前端还在
      // 等安装/刷新列表时，不能把弹窗提前关掉。
      if (p.active || p.error) {
        // 后端报错时 active 会变成 false，这里保持弹窗，让错误文案能显示出来。
        panel.market.download = { ...panel.market.download, ...p, active: true };
      }
    } catch (_) {}
    if (panel.market.download.active) panel.market.download.poll = setTimeout(tick, 350);
  };
  tick();
}

function stopDownloadProgress() {
  if (panel.market.download.poll) clearTimeout(panel.market.download.poll);
  if (panel.market.download.hideTimer) clearTimeout(panel.market.download.hideTimer);
  panel.market.download.poll = 0;
  const elapsed = Date.now() - Number(panel.market.download.startedAt || Date.now());
  // 失败时多留一会儿，让用户看清错误原因再关掉弹窗。
  const stay = panel.market.download.error ? 2600 : 650;
  const wait = Math.max(0, stay - elapsed);
  panel.market.download.hideTimer = setTimeout(() => {
    panel.market.download.active = false;
  }, wait);
}

export function hideDownloadProgress() {
  if (panel.market.download.poll) clearTimeout(panel.market.download.poll);
  if (panel.market.download.hideTimer) clearTimeout(panel.market.download.hideTimer);
  panel.market.download.poll = 0;
  panel.market.download.hideTimer = 0;
  panel.market.download.active = false;
}

export async function cancelDownload() {
  // 立刻收起弹窗，别让遮罩挡住面板；后端会在下一个分块中断开连接。
  panel.market.cancelling = true;
  hideDownloadProgress();
  toast("已取消下载");
  try { await apiPost("theme/market", { action: "cancel_download" }); } catch (_) {}
}

export async function marketDownload(id) {
  // 下载不覆盖当前主题，不需要额外确认；第一次点击就直接开始。
  panel.market.busy = true;
  // 先亮进度弹窗，点下去立刻有反馈；确认不冲突后再真正开始下载。
  startDownloadProgress();
  try {
    const ask = await apiPost("theme/market", { action: "download", url: panel.market.url, token: panel.market.token, id, mode: "ask" });
    if (ask.conflict) {
      hideDownloadProgress();
      panel.conflict = { show: true, incoming: ask.incoming, existing: ask.existing, name: ask.incoming?.name || "" };
      return;
    }
    try {
      await apiPost("theme/market", { action: "download", url: panel.market.url, token: panel.market.token, id, apply: false });
      await load();
      toast("已下载，请到「主题」页应用");
    } catch (error) {
      const msg = String(error.message || error);
      if (panel.market.cancelling) {
        hideDownloadProgress();
        return;
      }
      // 让弹窗停在“下载失败 + 原因”，而不是一闪而过。
      panel.market.download = { ...panel.market.download, active: true, error: msg, phase: "下载失败" };
      toast(`下载失败：${msg}`, "err");
    } finally {
      stopDownloadProgress();
    }
  } catch (error) {
    toast(`下载失败：${error.message || error}`, "err");
  } finally {
    panel.market.busy = false;
    panel.market.cancelling = false;
  }
}

export async function resolveConflict(mode) {
  const id = panel.conflict.incoming?.id;
  if (!id || mode === "cancel") { panel.conflict.show = false; return; }
  let name = String(panel.conflict.name || "").trim();
  if (mode === "rename" && !name) { toast("请输入新的主题名称", "err"); return; }
  if (mode === "rename") {
    const existingName = String(panel.conflict.existing?.name || "").trim();
    const incomingName = String(panel.conflict.incoming?.name || "").trim();
    // 名字和已有主题/下载主题一样时自动加随机后缀，避免还是重名。
    if (name === existingName || name === incomingName) name = `${name}-${Math.random().toString(36).slice(2, 6)}`;
  }
  panel.conflict.show = false;
  panel.market.busy = true;
  try {
    startDownloadProgress();
    try {
      await apiPost("theme/market", {
        action: "download", url: panel.market.url, token: panel.market.token,
        id, mode, name: mode === "rename" ? name : "", apply: false,
      });
      await load();
      await loadMy();
      toast("已下载，请到「主题」页应用");
    } catch (error) {
      const msg = String(error.message || error);
      if (panel.market.cancelling) {
        hideDownloadProgress();
        return;
      }
      panel.market.download = { ...panel.market.download, active: true, error: msg, phase: "下载失败" };
      toast(`下载失败：${msg}`, "err");
    } finally {
      stopDownloadProgress();
    }
  } catch (error) {
    toast(`下载失败：${error.message || error}`, "err");
  } finally {
    panel.market.busy = false;
    panel.market.cancelling = false;
  }
}

export async function openUploadDialog(targetId = "") {
  if (!panel.market.token) { toast("请先登录市场", "err"); return; }
  panel.uploadDialog.projectId = panel.projectId || panel.overview?.active_theme_id || panel.overview?.themes?.[0]?.id || "";
  panel.uploadDialog.targetId = targetId || "new";
  panel.uploadDialog.busy = false;
  panel.uploadDialog.show = true;
  try {
    const data = await apiPost("theme/market", {
      action: "my", url: panel.market.url, token: panel.market.token, kind: "published",
    });
    panel.myPublished = data.items || [];
  } catch (_) {
    panel.myPublished = (panel.market.items || []).filter((item) => item.mine);
  }
}

export async function marketUpload() {
  if (!panel.market.token) { toast("请先登录市场", "err"); return; }
  if (!panel.uploadDialog.projectId) { toast("请选择要上传的项目", "err"); return; }
  panel.uploadDialog.busy = true;
  const updating = Boolean(panel.uploadDialog.targetId) && panel.uploadDialog.targetId !== "new";
  try {
    const payload = {
      url: panel.market.url,
      token: panel.market.token,
      project_id: panel.uploadDialog.projectId,
    };
    let data;
    if (updating) {
      payload.action = "update_market";
      payload.id = panel.uploadDialog.targetId;
      data = await apiPost("theme/market", payload);
    } else {
      payload.action = "upload";
      data = await apiPost("theme/market", payload);
    }
    panel.uploadDialog.show = false;
    toast(`${updating ? "更新" : "上传"}成功：${data.item?.name || "已提交"}`);
    await marketSearch();
  } catch (error) {
    toast(`上传失败：${error.message || error}`, "err");
  } finally {
    panel.uploadDialog.busy = false;
  }
}

export function openMarketEdit(item) {
  panel.editDialog = {
    show: true, id: item.id, name: item.name || "",
    description: item.description || "", version: item.version || "1.0.0", busy: false,
  };
}

export async function saveMarketInfo() {
  if (!panel.editDialog.name.trim()) { toast("主题名不能为空", "err"); return; }
  panel.editDialog.busy = true;
  try {
    const data = await apiPost("theme/market", {
      action: "update_info", url: panel.market.url, token: panel.market.token,
      id: panel.editDialog.id, name: panel.editDialog.name,
      description: panel.editDialog.description, version: panel.editDialog.version,
    });
    const item = data.item || {};
    for (const list of [panel.market.items, panel.myItems, panel.myPublished]) {
      const found = (list || []).find((x) => x.id === panel.editDialog.id);
      if (found) Object.assign(found, item);
    }
    panel.editDialog.show = false;
    toast("市场信息已更新");
  } catch (error) {
    toast(`更新失败：${error.message || error}`, "err");
  } finally {
    panel.editDialog.busy = false;
  }
}

export async function marketDelete(id) {
  if (!(await confirm("从市场删除", "删除你在市场上传的这个主题？", true))) return;
  try {
    await apiPost("theme/market", { action: "delete_market", url: panel.market.url, token: panel.market.token, id });
    await marketSearch();
    toast("已从市场删除");
  } catch (error) {
    toast(`删除失败：${error.message || error}`, "err");
  }
}

/* ---------------- 设置 ---------------- */

export async function toggleBubble() {
  try {
    const data = await apiPost("settings", {
      bubble_enabled: Boolean(panel.settingsForm.bubble_enabled),
    });
    if (panel.overview) panel.overview.settings = data.settings || panel.overview.settings;
  } catch (error) {
    toast(`保存失败：${error.message || error}`, "err");
  }
}

export async function saveSettings() {
  try {
    const data = await apiPost("settings", {
      crossfade_ms: Number(panel.settingsForm.crossfade_ms || 0),
      bubble_enabled: Boolean(panel.settingsForm.bubble_enabled),
    });
    if (panel.overview) panel.overview.settings = data.settings || panel.overview.settings;
    toast("播放设置已保存");
  } catch (error) {
    toast(`保存失败：${error.message || error}`, "err");
  }
}

export async function sendBubble() {
  if (!panel.bubbleText.trim()) return;
  try {
    const data = await apiPost("event", { action: "bubble", text: panel.bubbleText.trim() });
    panel.live = data.state || panel.live;
    toast("气泡已发送");
  } catch (error) {
    toast(`发送失败：${error.message || error}`, "err");
  }
}

export async function clearBubble() {
  try {
    const data = await apiPost("event", { action: "bubble", text: "" });
    panel.live = data.state || panel.live;
    panel.bubbleText = "";
  } catch (error) {
    toast(`清空失败：${error.message || error}`, "err");
  }
}
