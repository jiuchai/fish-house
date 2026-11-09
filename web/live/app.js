(() => {
  const stage = document.getElementById("stage");
  const layers = [document.getElementById("videoA"), document.getElementById("videoB")];
  const empty = document.getElementById("empty");
  const statusEl = document.getElementById("status");
  const startBtn = document.getElementById("start");
  const soundBtn = document.getElementById("sound");
  const bubbleEl = document.getElementById("bubble");
  const bubbleText = document.getElementById("bubbleText");

  let config = { videos: {}, audio: {}, crossfade_ms: 400, scale_percent: 70 };
  let videos = {};
  let slotAudio = {};
  let crossfade = 400;
  let active = 0;
  let token = 0;
  let started = false;
  let currentMode = "rest";
  let currentSlot = "";
  let lastSeq = -1;
  let pendingOnce = null;
  let pendingAfterPhone = "";
  let pollBusy = false;
  let timerId = null;
  let currentFlowNode = "";
  let soundOn = true;
  let soundBlocked = false;
  let soundHintTimer = null;
  let audioRamp = 0;

  try {
    const savedSound = localStorage.getItem("fish_house_sound");
    if (savedSound === "0") soundOn = false;
  } catch (_) {}

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function srcFor(slot) {
    const raw = videos[slot];
    if (!raw) return "";
    if (/^(https?:|data:|blob:|\/)/i.test(raw)) return raw;
    return `./${raw.replace(/^\.?\//, "")}`;
  }

  function setStatus(text) {
    statusEl.textContent = text || "";
  }

  function updateSoundButton() {
    soundBtn.classList.toggle("muted", !soundOn);
    soundBtn.classList.toggle("attention", soundBlocked && !soundOn);
    if (soundBtn.classList.contains("hint")) return;
    soundBtn.textContent = soundOn ? "🔊" : "🔇";
    soundBtn.title = soundOn ? "关闭声音" : "开启声音";
    soundBtn.setAttribute("aria-label", soundBtn.title);
  }

  function audibleFor(slot) {
    return slotAudio[slot] !== false;
  }

  function applySound() {
    for (const layer of layers) {
      const audible = soundOn && audibleFor(layer.dataset.slot || "");
      layer.muted = !audible;
    }
    if (!audioRamp) {
      const current = layers[active];
      if (current && !current.muted) current.volume = 1;
    }
    updateSoundButton();
  }

  function stopAudioRamp() {
    if (audioRamp) {
      cancelAnimationFrame(audioRamp);
      audioRamp = 0;
    }
  }

  // 画面交叉淡化的同时做音量交叉：新片段 0→1，旧片段 1→0。
  function fadeAudio(from, to, duration) {
    stopAudioRamp();
    const ms = Math.max(0, Number(duration) || 0);
    if (to && !to.muted) to.volume = 0;
    if (from && !from.muted) from.volume = 1;
    if (ms <= 0) {
      if (to && !to.muted) to.volume = 1;
      if (from && !from.muted) from.volume = 0;
      return;
    }
    const started = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - started) / ms);
      if (to && !to.muted) to.volume = progress;
      if (from && !from.muted) from.volume = 1 - progress;
      if (progress < 1) {
        audioRamp = requestAnimationFrame(step);
      } else {
        audioRamp = 0;
        if (to) to.volume = 1;
        if (from) from.volume = 0;
      }
    };
    audioRamp = requestAnimationFrame(step);
  }

  function showSoundHint() {
    if (soundHintTimer) clearTimeout(soundHintTimer);
    soundBtn.classList.add("hint");
    soundBtn.textContent = "🔇 点我开声音";
    soundBtn.title = "开启声音";
    soundHintTimer = setTimeout(() => {
      soundHintTimer = null;
      soundBtn.classList.remove("hint");
      updateSoundButton();
    }, 8000);
  }

  async function playWithSound(video) {
    try {
      await video.play();
      return;
    } catch (error) {
      if (!(error && error.name === "NotAllowedError")) throw error;
      if (video.muted || !soundOn) throw error;
    }
    // 浏览器拦下带声音的自动播放：先静音播起来，点右上角喇叭再开声音。
    soundOn = false;
    soundBlocked = true;
    applySound();
    showSoundHint();
    await video.play();
  }

  function renderBubble(state) {
    if (!state) {
      bubbleEl.classList.add("hidden");
      return;
    }
    const text = String(state.bubble || "").trim();
    const until = Number(state.bubble_until || 0) * 1000;
    const active = Boolean(text) && (!until || until > Date.now());
    bubbleText.textContent = active ? text : "";
    bubbleEl.classList.toggle("hidden", !active);
  }

  function setEmpty(visible, text = "") {
    empty.classList.toggle("hidden", !visible);
    if (text) empty.querySelector(".empty-text").textContent = text;
  }

  function cancelPendingOnce() {
    if (pendingOnce) {
      const resolve = pendingOnce;
      pendingOnce = null;
      resolve();
    }
  }

  function pauseLayer(video) {
    video.onended = null;
    video.onerror = null;
    try { video.pause(); } catch (_) {}
  }

  function waitForFrame(video, timeout = 4000) {
    return new Promise((resolve) => {
      if (video.readyState >= 3) { resolve(); return; }
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        video.removeEventListener("loadeddata", finish);
        video.removeEventListener("canplay", finish);
        video.removeEventListener("playing", finish);
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(finish, timeout);
      video.addEventListener("loadeddata", finish);
      video.addEventListener("canplay", finish);
      video.addEventListener("playing", finish);
    });
  }

  function nextPaint() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
  }

  async function playClip(slot, { loop = false, onEnded = null } = {}) {
    const src = srcFor(slot);
    if (!src) return false;
    const nextIndex = 1 - active;
    const prev = layers[active];
    const next = layers[nextIndex];
    pauseLayer(next);
    try {
      next.dataset.slot = slot;
      next.src = src;
      next.loop = loop;
      next.muted = !(soundOn && audibleFor(slot));
      next.volume = 0;
      next.playsInline = true;
      next.preload = "auto";
      next.style.opacity = "0";
      // 新层永远叠在旧层上面，旧层保持不透明垫底，避免交叉淡化时透出背景。
      next.style.zIndex = "2";
      prev.style.zIndex = "1";
      next.load();
      // 等下一段有可播放帧再淡入，避免露出黑底或白底。
      await waitForFrame(next);
      next.currentTime = 0;
      await playWithSound(next);
      await nextPaint();
    } catch (error) {
      if (error && error.name === "NotAllowedError") {
        showStartHint();
      } else {
        setStatus("播放失败，等一下重试");
      }
      return false;
    }
    prev.onended = null;
    prev.onerror = null;
    setEmpty(false);
    // 第一段直接出现，不从白底淡入；之后才做旧层垫底的交叉淡入。
    const hadVisible = Number(prev.style.opacity) >= 1;
    if (hadVisible) {
      next.style.opacity = "1";
    } else {
      next.style.transition = "none";
      next.style.opacity = "1";
      void next.offsetWidth;
      next.style.transition = "";
    }
    active = nextIndex;
    currentSlot = slot;
    const old = prev;
    const fade = Math.max(0, Number(crossfade) || 0);
    fadeAudio(hadVisible ? old : null, next, hadVisible ? fade : 0);
    setTimeout(() => {
      if (old !== layers[active]) {
        old.volume = 0;
        old.style.opacity = "0";
        pauseLayer(old);
        old.removeAttribute("src");
        try { old.load(); } catch (_) {}
      }
    }, fade + 80);
    if (!loop) {
      next.onended = () => {
        next.onended = null;
        if (onEnded) onEnded();
      };
      next.onerror = () => {
        next.onended = null;
        if (onEnded) onEnded();
      };
    }
    return true;
  }

  function playOnce(slot) {
    if (!srcFor(slot)) return Promise.resolve();
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        if (pendingOnce === finish) pendingOnce = null;
        resolve();
      };
      pendingOnce = finish;
      playClip(slot, { loop: false, onEnded: finish }).then((ok) => {
        if (!ok) finish();
      }).catch(finish);
    });
  }

  async function playLoop(slot) {
    if (!srcFor(slot)) return false;
    return playClip(slot, { loop: true });
  }

  async function runRest() {
    currentFlowNode = "";
    currentMode = "rest";
    setEmpty(!srcFor("rest"), "还没有导入休息视频，先去插件面板导入吧");
    if (!srcFor("rest")) return;
    if (currentSlot === "rest" && !layers[active].paused) return;
    await playLoop("rest");
    setStatus("休息中");
  }

  async function phoneSequence(skipPickup = false) {
    const my = ++token;
    cancelPendingOnce();
    currentMode = "phone";
    if (!skipPickup && srcFor("phone_pickup")) {
      await playOnce("phone_pickup");
    }
    if (my !== token) return;
    if (srcFor("phone_loop")) {
      setStatus("在看手机");
      await playOnce("phone_loop");
    } else {
      await playLoop("rest");
    }
    if (my !== token) return;
    await finishPhone();
  }

  async function finishPhone() {
    const my = ++token;
    const nextMode = pendingAfterPhone;
    pendingAfterPhone = "";
    cancelPendingOnce();
    currentMode = "rest";
    if (srcFor("phone_putdown")) {
      await playOnce("phone_putdown");
    }
    if (my !== token) return;
    if (nextMode === "computer") {
      await startWork(false, true);
      return;
    }
    await runRest();
  }

  async function startWork(auto = false, skipPutdown = false) {
    if (currentMode === "computer") return;
    const my = ++token;
    cancelPendingOnce();
    const wasPhone = currentMode === "phone";
    currentMode = "computer";
    if (!skipPutdown && wasPhone && srcFor("phone_putdown")) {
      await playOnce("phone_putdown");
    }
    if (my !== token) return;
    if (srcFor("computer_go")) {
      await playOnce("computer_go");
    }
    if (my !== token) return;
    if (auto) {
      if (srcFor("computer_loop")) {
        setStatus("在电脑前干活");
        await playOnce("computer_loop");
      }
      if (my !== token) return;
      await endWork();
      return;
    }
    if (!(await playLoop("computer_loop"))) {
      await playLoop("rest");
    }
    if (my !== token) return;
    setStatus("在电脑前干活");
  }

  async function endWork() {
    if (currentMode !== "computer") {
      if (currentMode === "phone") await finishPhone();
      return;
    }
    const my = ++token;
    cancelPendingOnce();
    currentMode = "rest";
    if (srcFor("computer_rest")) {
      await playOnce("computer_rest");
    }
    if (my !== token) return;
    await runRest();
    setStatus("忙完啦，回窝里歇着");
  }

  function actionLabel(key) {
    const item = (config.actions || []).find((row) => row.key === key);
    return item ? item.label || key : key;
  }

  async function playCustomAction(key, loop) {
    if (!key) {
      await runRest();
      return;
    }
    if (!srcFor(key)) {
      // 刚在面板/接口里加的动作，页面配置可能还没轮询到，先拉一次。
      await refreshConfig();
    }
    if (!srcFor(key)) {
      await runRest();
      return;
    }
    const my = ++token;
    cancelPendingOnce();
    currentMode = "action";
    setStatus(actionLabel(key));
    if (loop) {
      await playLoop(key);
      return;
    }
    await playOnce(key);
    if (my !== token) return;
    await runRest();
  }

  async function playFlowNode(state) {
    const video = String(state.node_video || "");
    if (!video) {
      await runRest();
      return;
    }
    const my = ++token;
    cancelPendingOnce();
    currentMode = "flow";
    currentFlowNode = String(state.node_id || "");
    setStatus(String(state.node_label || "动作"));
    videos.__flow__ = video;
    const loop = Boolean(state.node_loop);
    const ok = await playClip("__flow__", { loop });
    if (!ok) {
      await runRest();
      return;
    }
    // 一次性节点：停在这里等插件按流程图切下一个节点（多出边时等模型选择）。
    if (my !== token) return;
  }

  function handleInitial(state) {
    renderBubble(state);
    currentMode = state.mode || "rest";
    pendingAfterPhone = String(state.after_phone || "");
    if (state.mode === "action") {
      void playCustomAction(String(state.action_key || ""), Boolean(state.action_loop));
      return;
    }
    if (state.mode === "flow") {
      void playFlowNode(state);
      return;
    }
    if (state.mode === "computer") {
      currentMode = "computer";
      if (state.auto_work && srcFor("computer_loop")) {
        playOnce("computer_loop").then(() => {
          if (currentMode === "computer") endWork();
        });
      } else if (srcFor("computer_loop")) {
        playLoop("computer_loop");
      } else {
        runRest();
      }
      setStatus("在电脑前");
      return;
    }
    if (state.mode === "phone") {
      currentMode = "phone";
      pendingAfterPhone = String(state.after_phone || "");
      if (srcFor("phone_loop")) {
        setStatus("在看手机");
        playOnce("phone_loop").then(() => {
          if (currentMode === "phone") finishPhone();
        });
      } else {
        runRest();
      }
      return;
    }
    runRest();
  }

  async function pollState() {
    if (!started || pollBusy) return;
    pollBusy = true;
    try {
      const state = await fetchJson(`./state.json?t=${Date.now()}`);
      renderBubble(state);
      const seq = Number(state.seq || 0);
      if (seq === lastSeq) return;
      lastSeq = seq;
      const mode = state.mode || "rest";
      if (mode === "action") {
        pendingAfterPhone = "";
        void playCustomAction(String(state.action_key || ""), Boolean(state.action_loop));
      } else if (mode === "flow") {
        pendingAfterPhone = "";
        if (currentFlowNode !== String(state.node_id || "")) {
          void playFlowNode(state);
        }
      } else if (mode === "rest") {
        pendingAfterPhone = "";
        if (currentMode === "computer") void endWork();
        else if (currentMode === "phone") void finishPhone();
        else void runRest();
      } else if (mode === "computer") {
        pendingAfterPhone = "";
        if (currentMode !== "computer") void startWork(Boolean(state.auto_work));
      } else if (mode === "phone") {
        pendingAfterPhone = String(state.after_phone || pendingAfterPhone || "");
        if (currentMode === "computer") return;
        if (currentMode !== "phone") void phoneSequence(false);
      }
    } catch (error) {
      setStatus("小屋连接中断，正在重试");
    } finally {
      pollBusy = false;
    }
  }

  function showStartHint() {
    started = false;
    if (timerId) { clearInterval(timerId); timerId = null; }
    startBtn.textContent = "点一下播放";
    startBtn.classList.remove("hidden");
    setStatus("浏览器拦下了自动播放，点一下画面继续");
  }

  async function beginPlayback() {
    if (started) return;
    started = true;
    startBtn.classList.add("hidden");
    if (timerId) clearInterval(timerId);
    timerId = setInterval(pollState, 500);
    try {
      const state = await fetchJson(`./state.json?t=${Date.now()}`);
      lastSeq = Number(state.seq || 0);
      handleInitial(state);
    } catch (_) {
      await runRest();
    }
  }

  async function refreshConfig() {
    try {
      const data = await fetchJson(`./config.json?t=${Date.now()}`);
      videos = data.videos || videos;
      slotAudio = data.audio || {};
      config.actions = data.actions || config.actions;
      const nextFade = Math.max(0, Number(data.crossfade_ms || 400));
      if (nextFade !== crossfade) {
        crossfade = nextFade;
        stage.style.setProperty("--fade", `${nextFade}ms`);
      }
      applySound();
    } catch (_) {}
  }

  async function boot() {
    try {
      config = await fetchJson(`./config.json?t=${Date.now()}`);
      videos = config.videos || {};
      slotAudio = config.audio || {};
      crossfade = Math.max(0, Number(config.crossfade_ms || 400));
      stage.style.setProperty("--scale", String(Math.max(20, Number(config.scale_percent || 70)) / 100));
      stage.style.setProperty("--fade", `${crossfade}ms`);
      setEmpty(!videos.rest, "还没有导入休息视频，先去插件面板导入吧");
    } catch (error) {
      setStatus("还没有配置，先去插件面板导入视频");
      setEmpty(true);
    }
    // 打开就播：默认带声音；浏览器拦下带声音的自动播放时先静音播，点喇叭开声音。
    await beginPlayback();
    setInterval(refreshConfig, 8000);
  }

  startBtn.addEventListener("click", () => {
    beginPlayback();
  });

  stage.addEventListener("click", () => {
    if (!started) beginPlayback();
  });

  soundBtn.addEventListener("click", async () => {
    if (soundHintTimer) { clearTimeout(soundHintTimer); soundHintTimer = null; }
    soundBtn.classList.remove("hint");
    soundOn = !soundOn;
    soundBlocked = false;
    applySound();
    try { localStorage.setItem("fish_house_sound", soundOn ? "1" : "0"); } catch (_) {}
    if (!soundOn) return;
    const video = layers[active];
    try {
      await video.play();
    } catch (_) {}
  });

  boot();
  updateSoundButton();
})();
