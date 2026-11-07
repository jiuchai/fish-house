(() => {
  const inIframe = window.self !== window.top;
  let bridgeClient = null;
  const SLOT_META = [
    ["rest", "休息循环", "啥也不干时循环播放"],
    ["phone_pickup", "拿起手机", "收到消息，伸手拿手机"],
    ["phone_loop", "玩手机 / 回消息", "看手机、回消息时播放"],
    ["phone_putdown", "放下手机", "回复完，把手机放回去"],
    ["computer_go", "去电脑", "从休息处走到电脑前"],
    ["computer_loop", "玩电脑 / 干活", "干活时循环"],
    ["computer_rest", "电脑回休息", "干完活，从电脑回到休息"],
  ];
  const slotsEl = document.getElementById("slots");
  const statePill = document.getElementById("statePill");
  const stateText = document.getElementById("stateText");
  const publicUrl = document.getElementById("publicUrl");
  const publicHint = document.getElementById("publicHint");
  const toastEl = document.getElementById("toast");
  let overview = null;
  let pollTimer = null;

  const API_MARKER = "/api/v1/plugins/";
  const markerIndex = window.location.pathname.indexOf(API_MARKER);
  const APP_PREFIX = markerIndex >= 0 ? window.location.pathname.slice(0, markerIndex) : "";
  const API_BASE = `${APP_PREFIX}/api/v1/plugins/extensions/astrbot_plugin_fish_house`;
  const PUBLIC_FALLBACK = `${APP_PREFIX}/fish_house/index.html`;

  // AstrBot 的插件页放在 sandbox iframe 里（没有 allow-same-origin），
  // 直接 fetch 一定报 Failed to fetch；只能走 window.AstrBotPluginView 桥接。
  function bridgeView() {
    if (bridgeClient) return bridgeClient;
    return inIframe ? window.AstrBotPluginView || null : null;
  }

  async function bridgeRequest(path, params, body, method) {
    const view = bridgeView();
    if (!view) throw new Error("插件页桥接还没有准备好");
    const data = method === "GET"
      ? await view.apiGet(path, params || {})
      : await view.apiPost(path, body || {});
    if (data && data.status === "error") {
      throw new Error(data.message || "接口返回错误");
    }
    return data || {};
  }

  async function cookieRequest(path, params, body, method) {
    const url = new URL(`${API_BASE}/${path}`, window.location.origin);
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    }
    const response = await fetch(url, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status === "error") {
      throw new Error(data.message || `HTTP ${response.status}`);
    }
    return data;
  }

  async function apiRequest(path, params, body, method) {
    const view = bridgeView();
    let firstError = null;
    if (view) {
      try {
        return await bridgeRequest(path, params, body, method);
      } catch (error) {
        firstError = error;
      }
    }
    try {
      return await cookieRequest(path, params, body, method);
    } catch (error) {
      throw firstError || error;
    }
  }

  function apiGet(path, params) {
    return apiRequest(path, params, null, "GET");
  }

  function apiPost(path, body) {
    return apiRequest(path, null, body || {}, "POST");
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function toast(message, kind = "") {
    toastEl.textContent = message;
    toastEl.className = `toast show ${kind}`;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toastEl.className = "toast"; }, 2600);
  }

  function stateLabel(mode) {
    return mode === "computer" ? "电脑" : mode === "phone" ? "手机" : "休息";
  }

  function renderState(state) {
    const mode = state?.mode || "rest";
    statePill.textContent = stateLabel(mode);
    statePill.className = `pill ${mode === "computer" ? "on" : mode === "phone" ? "warm" : ""}`;
    const until = Number(state?.phone_until || 0);
    stateText.textContent = mode === "phone" && until
      ? `回消息中 · 约 ${Math.max(0, Math.round(until - Date.now() / 1000))}s`
      : `状态序号 ${state?.seq || 0}`;
  }

  function slotCard(slot) {
    const imported = Boolean(slot.url);
    const preview = slot.preview_url || slot.url || "";
    const audible = slot.audio !== false;
    return `<article class="slot" data-key="${esc(slot.key)}">
      <div class="slot-head">
        <h3>${esc(slot.label)}</h3>
        <span class="pill ${imported ? "on" : ""}">${imported ? "已导入" : "空"}</span>
      </div>
      ${imported && preview
        ? `<video src="${esc(preview)}" muted playsinline preload="metadata"></video>`
        : `<div class="placeholder">${esc(slot.hint)}</div>`}
      <div class="slot-meta">${esc(slot.filename || slot.url || "还没有视频")}${slot.duration ? ` · ${Number(slot.duration).toFixed(1)}s` : ""}</div>
      <div class="slot-actions">
        <label class="btn small primary">上传视频<input class="file-input" data-slot="${esc(slot.key)}" type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov,.m4v" hidden /></label>
        <button class="btn small" data-action="slot-url" data-slot="${esc(slot.key)}" type="button">用地址</button>
        <button class="btn small danger" data-action="slot-clear" data-slot="${esc(slot.key)}" type="button" ${imported ? "" : "disabled"}>清除</button>
      </div>
      <label class="audio-toggle">
        <input class="audio-input" data-slot="${esc(slot.key)}" type="checkbox" ${audible ? "checked" : ""} />
        播放有声
      </label>
      <div class="progress" data-progress="${esc(slot.key)}"><i></i></div>
    </article>`;
  }

  function renderSkeleton() {
    slotsEl.innerHTML = SLOT_META.map(([key, label, hint]) =>
      slotCard({ key, label, hint, url: "", preview_url: "", filename: "", duration: 0 })
    ).join("");
    if (!publicUrl.value) {
      publicUrl.value = `${window.location.origin}${PUBLIC_FALLBACK}`;
    }
  }

  function render() {
    if (!overview) return;
    slotsEl.innerHTML = (overview.slots || []).map(slotCard).join("");
    publicUrl.value = `${window.location.origin}${PUBLIC_FALLBACK}`;
    const exportLink = document.getElementById("exportPlugin");
    exportLink.href = overview.export_url || `${window.location.origin}${APP_PREFIX}/fish_house/astrbot_plugin_fish_house.zip`;
    document.getElementById("exportHint").textContent = overview.public_ready
      ? "zip 已生成，可直接下载；导入时解压到 data/plugins/"
      : "公共目录不可用，zip 可能没有生成。";
    publicHint.textContent = overview.public_ready
      ? "公共页已就绪；修改视频或设置后会自动同步。"
      : "公共页还没同步成功，检查 AstrBot 的 dashboard/dist 是否存在。";
    const settings = overview.settings || {};
    document.getElementById("crossfade").value = settings.crossfade_ms ?? 400;
    document.getElementById("autoReply").checked = Boolean(Number(settings.auto_reply ?? 1));
    if (document.activeElement !== document.getElementById("bubbleText")) {
      document.getElementById("bubbleText").value = overview.state?.bubble || "";
    }
    const endpoint = `${window.location.origin}${API_BASE}/event`;
    document.getElementById("apiEndpoint").textContent = endpoint;
    document.getElementById("apiExample").textContent = `curl -X POST "${endpoint}" \n  -H "Authorization: Bearer <AstrBot API Key>" \n  -H "Content-Type: application/json" \n  -d '{"action":"work_start"}'`;
    renderState(overview.state);
  }

  async function loadBackups() {
    const select = document.getElementById("backupSelect");
    const hint = document.getElementById("backupHint");
    try {
      const data = await apiGet("backups");
      const backups = data.backups || [];
      select.innerHTML = "";
      if (!backups.length) {
        select.innerHTML = '<option value="">还没有备份</option>';
        hint.textContent = "还没有备份；下次更新或清空槽位前会自动生成。";
        return;
      }
      for (const item of backups) {
        const option = document.createElement("option");
        option.value = item.id;
        const time = item.created_at ? new Date(item.created_at * 1000).toLocaleString() : item.id;
        option.textContent = `${time} · ${item.reason || "自动"} · ${item.videos.length} 个视频`;
        select.appendChild(option);
      }
      hint.textContent = `共 ${backups.length} 个备份，保留最近 20 个。`;
    } catch (error) {
      hint.textContent = `读取备份失败：${error.message || error}`;
    }
  }

  async function load() {
    try {
      overview = await apiGet("overview");
      const errorBox = document.getElementById("apiError");
      if (errorBox) { errorBox.textContent = ""; errorBox.classList.add("hidden"); }
      render();
      loadBackups();
      return true;
    } catch (error) {
      renderSkeleton();
      const message = error.message || error;
      const errorBox = document.getElementById("apiError");
      if (errorBox) {
        errorBox.textContent = `接口请求失败：${message}（${API_BASE}）`;
        errorBox.classList.remove("hidden");
      }
      publicHint.textContent = `接口请求失败：${message}。接口地址：${API_BASE}`;
      toast(`加载失败：${message}`, "err");
      return false;
    }
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(loadState, 1500);
  }

  async function loadState() {
    try {
      const data = await apiGet("state");
      renderState(data.state);
    } catch (_) {}
  }

  async function trigger(action) {
    try {
      const data = await apiPost("event", { action });
      renderState(data.state);
      toast(`已切换：${stateLabel(data.state?.mode)}`);
      if (overview) overview.state = data.state;
    } catch (error) {
      toast(`切换失败：${error.message || error}`, "err");
    }
  }

  function setProgress(slot, ratio) {
    const wrap = document.querySelector(`[data-progress="${CSS.escape(slot)}"]`);
    if (!wrap) return;
    wrap.classList.add("show");
    const bar = wrap.querySelector("i");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, ratio * 100))}%`;
  }

  function clearProgress(slot) {
    const wrap = document.querySelector(`[data-progress="${CSS.escape(slot)}"]`);
    if (wrap) {
      wrap.classList.remove("show");
      const bar = wrap.querySelector("i");
      if (bar) bar.style.width = "0%";
    }
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const step = 0x8000;
    for (let i = 0; i < bytes.length; i += step) {
      binary += String.fromCharCode(...bytes.subarray(i, i + step));
    }
    return btoa(binary);
  }

  async function uploadFile(slot, file) {
    if (!file) return;
    try {
      setProgress(slot, 0);
      const start = await apiPost("upload/start", {
        slot,
        filename: file.name,
        size: file.size,
      });
      const chunkSize = Number(start.chunk_size || 512 * 1024);
      const total = Math.max(1, Math.ceil(file.size / chunkSize));
      for (let index = 0; index < total; index += 1) {
        const begin = index * chunkSize;
        const end = Math.min(file.size, begin + chunkSize);
        const buffer = await file.slice(begin, end).arrayBuffer();
        await apiPost("upload/chunk", {
          upload_id: start.upload_id,
          index,
          data: arrayBufferToBase64(buffer),
        });
        setProgress(slot, (index + 1) / total);
      }
      await apiPost("upload/finish", { upload_id: start.upload_id });
      toast(`${SLOT_LABEL(slot)} 视频已导入`);
      await load();
    } catch (error) {
      clearProgress(slot);
      toast(`上传失败：${error.message || error}`, "err");
    } finally {
      setTimeout(() => clearProgress(slot), 800);
    }
  }

  function SLOT_LABEL(key) {
    return overview?.slots?.find((slot) => slot.key === key)?.label || key;
  }

  async function saveSettings() {
    try {
      const payload = {
        crossfade_ms: Number(document.getElementById("crossfade").value || 0),
        auto_reply: Boolean(document.getElementById("autoReply").checked),
      };
      const data = await apiPost("settings", payload);
      if (overview) overview.settings = data.settings;
      toast("播放设置已保存");
    } catch (error) {
      toast(`保存失败：${error.message || error}`, "err");
    }
  }

  document.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (!action) return;
    if (["reply", "work_start", "work_end", "rest"].includes(action)) {
      await trigger(action);
      return;
    }
    const slot = event.target.closest("[data-slot]")?.dataset.slot;
    if (action === "slot-clear" && slot) {
      try {
        await apiPost("slot", { slot, url: "" });
        toast("已清除");
        await load();
      } catch (error) { toast(`清除失败：${error.message || error}`, "err"); }
    }
    if (action === "slot-url" && slot) {
      const url = window.prompt("输入视频地址（http(s) 链接，或 /fish_house/... 路径）");
      if (!url) return;
      try {
        await apiPost("slot", { slot, url: url.trim() });
        toast("地址已保存");
        await load();
      } catch (error) { toast(`保存失败：${error.message || error}`, "err"); }
    }
  });

  slotsEl.addEventListener("change", async (event) => {
    const audioInput = event.target.closest(".audio-input");
    if (audioInput) {
      const slot = audioInput.dataset.slot;
      const checked = audioInput.checked;
      try {
        await apiPost("settings", { audio: { [slot]: checked } });
        if (overview?.slots) {
          const row = overview.slots.find((item) => item.key === slot);
          if (row) row.audio = checked;
        }
        toast(checked ? "这段视频会出声" : "这段视频已静音");
      } catch (error) {
        audioInput.checked = !checked;
        toast(`保存失败：${error.message || error}`, "err");
      }
      return;
    }
    const input = event.target.closest(".file-input");
    if (!input) return;
    const file = input.files?.[0];
    const slot = input.dataset.slot;
    input.value = "";
    if (file && slot) uploadFile(slot, file);
  });

  document.getElementById("copyLink").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(publicUrl.value);
      toast("公共链接已复制");
    } catch (_) {
      publicUrl.select();
      document.execCommand("copy");
      toast("公共链接已复制");
    }
  });

  document.getElementById("openLink").addEventListener("click", () => {
    if (publicUrl.value) window.open(publicUrl.value, "_blank", "noopener");
  });
  document.getElementById("saveSettings").addEventListener("click", saveSettings);
  document.getElementById("sendBubble").addEventListener("click", async () => {
    const text = document.getElementById("bubbleText").value.trim();
    if (!text) { toast("先写一句气泡内容", "err"); return; }
    try {
      const data = await apiPost("event", { action: "bubble", text });
      renderState(data.state);
      toast("气泡已发送");
    } catch (error) { toast(`发送失败：${error.message || error}`, "err"); }
  });
  document.getElementById("clearBubble").addEventListener("click", async () => {
    try {
      const data = await apiPost("event", { action: "bubble", text: "" });
      renderState(data.state);
      toast("气泡已清空");
    } catch (error) { toast(`清空失败：${error.message || error}`, "err"); }
  });

  document.getElementById("restoreBackup").addEventListener("click", async () => {
    const select = document.getElementById("backupSelect");
    const id = select?.value || "";
    if (!id) { toast("先选一个备份", "err"); return; }
    if (!window.confirm("恢复会用备份里的视频和槽位配置覆盖当前内容，继续吗？")) return;
    try {
      await apiPost("restore", { id });
      toast("备份已恢复");
      await load();
    } catch (error) { toast(`恢复失败：${error.message || error}`, "err"); }
  });

  document.getElementById("copyApi").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById("apiEndpoint").textContent);
      toast("接口地址已复制");
    } catch (_) {
      toast("复制失败，请手动选择");
    }
  });
  document.getElementById("refreshSync").addEventListener("click", async () => {
    try {
      const data = await apiPost("sync", {});
      toast(data.public_ready ? "公共页已重新同步" : "同步失败：找不到 dashboard/dist", data.public_ready ? "" : "err");
      await load();
    } catch (error) { toast(`同步失败：${error.message || error}`, "err"); }
  });

  async function connectBridge() {
    if (!inIframe) return null;
    const view = window.AstrBotPluginView;
    if (!view) return null;
    try {
      await Promise.race([
        view.ready(),
        new Promise((_, reject) => setTimeout(() => reject(new Error("bridge timeout")), 3000)),
      ]);
      return view;
    } catch (_) {
      return null;
    }
  }

  (async () => {
    renderSkeleton();
    // 插件页在 sandbox iframe 里：先等桥接就绪，再发请求。
    bridgeClient = await connectBridge();
    if (await load()) {
      startPolling();
      return;
    }
    if (!inIframe) {
      const fallback = document.createElement("main");
      fallback.className = "standalone";
      fallback.innerHTML = `<h1>大肥鱼的小屋</h1>
        <p>这是插件管理页，需要从 AstrBot 插件面板打开；直接打开时需要先在同一个浏览器里登录 AstrBot。</p>
        <div class="standalone-actions">
          <a class="btn primary" href="/extension">打开 AstrBot 插件管理</a>
          <a class="btn" href="${PUBLIC_FALLBACK}" target="_blank" rel="noopener">打开公共桌宠</a>
        </div>
        <p class="hint">上传视频、复制公共链接都在管理页里。</p>`;
      document.body.innerHTML = "";
      document.body.appendChild(fallback);
      return;
    }
    // 桥接暂时失败的：保留槽位和错误提示，继续轮询重试。
    startPolling();
  })();
})();
