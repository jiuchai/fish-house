(() => {
  const stage = document.getElementById("stage");
  const layers = [document.getElementById("videoA"), document.getElementById("videoB")];
  const empty = document.getElementById("empty");
  const statusEl = document.getElementById("status");
  const startBtn = document.getElementById("start");
  const soundBtn = document.getElementById("sound");
  const bubbleEl = document.getElementById("bubble");
  const bubbleText = document.getElementById("bubbleText");

  let config = { crossfade_ms: 400 };
  let bubbleTimer = 0;
  let crossfade = 400;
  let lastState = null;
  let active = 0;
  let token = 0;
  let started = false;
  let lastSeq = -1;
  let lastNodeKey = "";
  let currentKey = "";
  let soundOn = true;
  let soundBlocked = false;
  let soundHintTimer = null;
  let audioRamp = 0;
  let socket = null;
  let reconnectTimer = null;

  try {
    const savedSound = localStorage.getItem("fish_house_sound");
    if (savedSound === "0") soundOn = false;
  } catch (_) {}

  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    });
  }

  function srcFor(raw) {
    const value = String(raw || "");
    if (!value) return "";
    if (/^(https?:|data:|blob:|\/)/i.test(value)) return value;
    return `./${value.replace(/^\.?\//, "")}`;
  }

  function setStatus(text) {
    statusEl.textContent = text || "";
  }

  function updateSoundButton() {
    soundBtn.classList.toggle("muted", !soundOn);
    soundBtn.classList.toggle("attention", soundBlocked && !soundOn);
    if (soundBtn.classList.contains("hint")) return;
    const textEl = soundBtn.querySelector(".sound-text");
    if (textEl) textEl.textContent = "";
    soundBtn.title = soundOn ? "关闭声音" : "开启声音";
    soundBtn.setAttribute("aria-label", soundBtn.title);
  }

  function applySound() {
    for (const layer of layers) layer.muted = !soundOn;
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
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - startedAt) / ms);
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
    const textEl = soundBtn.querySelector(".sound-text");
    if (textEl) textEl.textContent = "点我开声音";
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
    const raw = String(state.bubble || "").trim();
    const until = Number(state.bubble_until || 0) * 1000;
    const alive = Boolean(raw) && (!until || until > Date.now());
    const visible = config.show_bubble !== false && alive;
    if (visible) {
      if (bubbleText.textContent !== raw) bubbleText.textContent = raw;
      // 下一帧再显示，保证从透明到可见有淡入过渡
      requestAnimationFrame(() => bubbleEl.classList.remove("hidden"));
    } else {
      // 只做淡出，文字留到完全隐形后再清，避免闪一下
      bubbleEl.classList.add("hidden");
    }
    // 到时间点主动收起，不依赖下一次状态推送
    if (bubbleTimer) { clearTimeout(bubbleTimer); bubbleTimer = 0; }
    if (visible && until > Date.now()) {
      bubbleTimer = setTimeout(() => {
        bubbleEl.classList.add("hidden");
      }, until - Date.now());
    }
  }

  function setEmpty(visible, text = "") {
    empty.classList.toggle("hidden", !visible);
    if (text) empty.querySelector(".empty-text").textContent = text;
  }

  function pauseLayer(video) {
    video.onended = null;
    video.onerror = null;
    if (video._loopEnd) {
      video.removeEventListener("timeupdate", video._loopEnd);
      video._loopEnd = null;
    }
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

  async function playClip(raw, { loop = false } = {}) {
    const src = srcFor(raw);
    if (!src) return false;
    const nextIndex = 1 - active;
    const prev = layers[active];
    const next = layers[nextIndex];
    pauseLayer(next);
    try {
      next.src = src;
      next.loop = loop;
      next.muted = !soundOn;
      next.volume = 0;
      next.playsInline = true;
      next.preload = "auto";
      next.style.opacity = "0";
      // 循环视频播到结尾（首尾衔接点）时，回报后端，让后端在衔接点切下一个节点。
      if (loop) {
        let lastT = 0;
        next._loopEnd = () => {
          const t = next.currentTime;
          // currentTime 突然回退超过 0.5 秒，说明这一轮循环结束了。
          if (lastT > 0 && t + 0.5 < lastT) {
            reportLoopEnd();
          }
          lastT = t;
        };
        next.addEventListener("timeupdate", next._loopEnd);
      } else {
        next._loopEnd = null;
      }
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
    return true;
  }

  async function playNode(state) {
    const video = String(state.node_video || "");
    const label = String(state.node_label || "");
    currentKey = `${state.node_id || ""}|${video}`;
    if (!video) {
      pauseLayer(layers[active]);
      layers[active].removeAttribute("src");
      try { layers[active].load(); } catch (_) {}
      setEmpty(true, "这个主题还没配视频，去插件面板的画布给节点加一段吧");
      setStatus("");
      return;
    }
    const my = ++token;
    setStatus(label);
    const ok = await playClip(video, { loop: Boolean(state.node_loop) });
    if (!ok || my !== token) return;
    reportPlaying(state.node_id);
    // 一次性节点停在这里，等插件按画布自动切到下一个节点。
  }

  // 视频真正开始播放后才回报节点，面板的高亮以这个为准，避免提前高亮下一个节点。
  function reportPlaying(nodeId) {
    if (!nodeId || !socket || socket.readyState !== WebSocket.OPEN) return;
    try { socket.send(JSON.stringify({ type: "playing", node_id: String(nodeId) })); } catch (_) {}
  }

  // 循环视频播到首尾衔接点后，回报后端，让后端在这个点切下一个节点。
  function reportLoopEnd() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    try { socket.send(JSON.stringify({ type: "loop_end" })); } catch (_) {}
  }

  function handleInitial(state) {
    lastState = state;
    renderBubble(state);
    lastNodeKey = `${state.node_id || ""}|${state.node_video || ""}`;
    void playNode(state);
  }

  function applyState(state) {
    if (!started) return;
    lastState = state;
    renderBubble(state);
    const seq = Number(state.seq || 0);
    if (seq === lastSeq) return;
    lastSeq = seq;
    const key = `${state.node_id || ""}|${state.node_video || ""}`;
    if (key === lastNodeKey && key === currentKey && !layers[active].paused) {
      setStatus(String(state.node_label || ""));
      return;
    }
    lastNodeKey = key;
    void playNode(state);
  }

  function connectLive() {
    if (socket) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    let current;
    try {
      current = new WebSocket(`${protocol}//${location.host}/ws`);
    } catch (_) {
      return;
    }
    socket = current;
    current.onopen = () => {
      setStatus("");
    };
    current.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === "state" && message.state) applyState(message.state);
      else if (message.type === "config" && message.config) applyConfig(message.config);
    };
    current.onclose = () => {
      if (socket !== current) return;
      socket = null;
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connectLive, 3000);
    };
    current.onerror = () => {};
  }

  function showStartHint() {
    started = false;
    startBtn.textContent = "点一下播放";
    startBtn.classList.remove("hidden");
    setStatus("浏览器拦下了自动播放，点一下画面继续");
  }

  async function beginPlayback() {
    if (started) return;
    started = true;
    startBtn.classList.add("hidden");
    try {
      const state = await fetchJson("./state.json");
      lastSeq = Number(state.seq || 0);
      handleInitial(state);
    } catch (_) {
      setStatus("还没有配置，先去插件面板选一个主题");
      setEmpty(true);
    }
  }

  function applyConfig(data) {
    config = { ...config, ...data };
    const nextFade = Math.max(0, Number(data.crossfade_ms || 400));
    if (nextFade !== crossfade) {
      crossfade = nextFade;
      stage.style.setProperty("--fade", `${nextFade}ms`);
    }
    applySound();
    if (lastState) renderBubble(lastState);
  }

  async function boot() {
    try {
      config = await fetchJson("./config.json");
      crossfade = Math.max(0, Number(config.crossfade_ms || 400));
      stage.style.setProperty("--fade", `${crossfade}ms`);
    } catch (error) {
      setStatus("还没有配置，先去插件面板选一个主题");
    }
    // 打开就播：默认带声音；浏览器拦下带声音的自动播放时先静音播，点喇叭开声音。
    await beginPlayback();
    connectLive();
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
