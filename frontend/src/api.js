export const THEME_TOKEN_KEY = "fishHouseThemeToken";
export const THEME_MARKET_KEY = "fishHouseThemeMarket";
export const THEME_USER_KEY = "fishHouseThemeUser";

const memoryStore = {};

export function storageGet(key) {
  try { return window.localStorage.getItem(key); }
  catch (_) { return Object.prototype.hasOwnProperty.call(memoryStore, key) ? memoryStore[key] : null; }
}
export function storageSet(key, value) {
  try { window.localStorage.setItem(key, String(value)); }
  catch (_) { memoryStore[key] = String(value); }
}

// 面板由插件自己的服务（默认 47613）托管，接口与页面同源。
const API_BASE = "/api";

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
  // Vue 的响应式对象是 Proxy，统一先做一次 JSON 深拷贝，去掉 Proxy 与函数。
  const safeBody = body == null ? body : JSON.parse(JSON.stringify(body));
  return cookieRequest(path, params, safeBody, method);
}
export const apiGet = (path, params) => apiRequest(path, params, null, "GET");
export const apiPost = (path, body) => apiRequest(path, null, body || {}, "POST");

export function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
