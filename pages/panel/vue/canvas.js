window.CanvasPanel = {
  props: { flow: Object, state: Object, library: Array, toast: Function, confirm: Function, readonly: Boolean, projectMode: Boolean },
  emits: ["flow-changed", "state-changed", "save-project"],
  data() {
    return {
      nodes: [], edges: [], sizes: {}, nodeEls: {},
      view: { x: 24, y: 24, scale: 1 },
      selectedId: "", selectedEdgeId: "", link: null, drag: null, pan: null, fitDone: false, rightRail: 1150,
    };
  },
  computed: {
    worldStyle() {
      return { transform: `translate(${this.view.x}px, ${this.view.y}px) scale(${this.view.scale})` };
    },
    selected() { return this.nodes.find((n) => n.id === this.selectedId) || null; },
    currentId() {
      const s = this.state || {};
      if (s.mode === "flow") return String(s.node_id || "");
      if (s.mode === "rest") return ((this.nodes.find((n) => n.role === "rest") || this.nodes.find((n) => n.role === "wait")) || {}).id || "";
      if (s.node_id && this.nodes.some((n) => n.id === s.node_id)) return String(s.node_id);
      const role = s.mode === "computer" ? "work" : s.mode === "phone" ? "phone" : "";
      return role ? (this.nodes.find((n) => n.role === role) || {}).id || "" : "";
    },
    currentLabel() {
      const node = this.nodes.find((n) => n.id === this.currentId);
      return node ? `${node.label} · ${this.roleLabel(node.role)}` : "—";
    },
  },
  watch: {
    flow: { deep: true, handler(value) { if (value) this.loadFlow(value); } },
  },
  mounted() {
    this.loadFlow(this.flow || { nodes: [], edges: [] });
    window.addEventListener("mousemove", this.onMove);
    window.addEventListener("mouseup", this.onUp);
    window.addEventListener("keydown", this.onKeyDown);
  },
  beforeUnmount() {
    window.removeEventListener("mousemove", this.onMove);
    window.removeEventListener("mouseup", this.onUp);
    window.removeEventListener("keydown", this.onKeyDown);
  },
  methods: {
    nodeCopies(node) {
      return node.role === "wait" ? ["left", "right"] : ["single"];
    },
    nodeLeft(node, copy) {
      if (node.role !== "wait") return node.x;
      return copy === "right" ? this.rightRail : 0;
    },
    onRoleChange(role) {
      if (!this.selected) return;
      this.selected.role = role;
      this.selected.loop = ["wait", "both", "work", "phone"].includes(role);
      if (role === "wait") {
        const exists = this.edges.some((e) => e.from === this.selected.id);
        const pickup = this.nodes.find((n) => n.id === "phone_pickup") || this.nodes.find((n) => n.role === "phone");
        if (!exists && pickup) {
          this.edges.push({ id: `e_${this.selected.id}_msg`, from: this.selected.id, to: pickup.id, label: "收到消息" });
        }
      }
    },
    roleLabel(role) {
      return { wait: "循环等待", both: "回消息/工作", work: "仅工作", phone: "仅回消息", rest: "循环等待", action: "动作", transition: "过渡" }[role] || "动作";
    },
    roleDescription(role) {
      return {
        wait: "循环等待：停在这里等消息，收到消息后按出边继续",
        both: "回消息/工作：可以在这里回复消息，也可以接任务；无任务一分钟走向下一个节点",
        work: "仅工作：只接任务，不处理消息；无任务一分钟走向下一个节点",
        phone: "仅回消息：只回复消息，不接工作；收到消息时进入",
        rest: "循环等待：停在这里等消息，收到消息后按出边继续",
        action: "动作：播一次后按出边继续",
        transition: "过渡：播完按出边继续",
      }[role] || "动作：播一次后按出边继续";
    },
    loadFlow(flow) {
      this.nodes = JSON.parse(JSON.stringify(flow.nodes || []));
      this.edges = JSON.parse(JSON.stringify(flow.edges || []));
      for (const node of this.nodes) {
        if (["wait", "both", "work", "phone", "rest"].includes(node.role)) node.loop = true;
        else if (["action", "transition"].includes(node.role)) node.loop = false;
      }
      if (this.selectedId && !this.nodes.some((n) => n.id === this.selectedId)) this.selectedId = "";
      if (this.selectedEdgeId && !this.edges.some((e) => e.id === this.selectedEdgeId)) this.selectedEdgeId = "";
      this.$nextTick(() => {
        this.measure();
        if (!this.fitDone) { this.fit(); this.fitDone = true; }
        setTimeout(() => this.refit(), 220);
      });
    },
    measure() {
      for (const node of this.nodes) {
        const el = this.nodeEls[node.id];
        if (el && el.offsetWidth > 0) this.sizes[node.id] = { w: el.offsetWidth, h: el.offsetHeight };
      }
    },
    setNodeRef(id, el) {
      if (el) this.nodeEls[id] = el;
      else delete this.nodeEls[id];
    },
    refit() {
      this.measure();
      this.fit();
    },
    fit() {
      if (!this.nodes.length || !this.$refs.canvas) return;
      const rect = this.$refs.canvas.getBoundingClientRect();
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of this.nodes) {
        const size = this.sizes[n.id] && this.sizes[n.id].w > 0 ? this.sizes[n.id] : { w: 190, h: 96 };
        const left = n.role === "wait" ? 0 : n.x;
        const right = n.role === "wait" ? this.rightRail : n.x;
        minX = Math.min(minX, left); minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, right + size.w); maxY = Math.max(maxY, n.y + size.h);
      }
      const pad = 34, spanX = Math.max(1, maxX - minX), spanY = Math.max(1, maxY - minY);
      const scale = Math.max(0.45, Math.min(1.12, (rect.width - pad * 2) / spanX, (rect.height - pad * 2) / spanY));
      this.view = {
        scale,
        x: (rect.width - spanX * scale) / 2 - minX * scale,
        y: (rect.height - spanY * scale) / 2 - minY * scale,
      };
    },
    edgePath(edge) {
      const from = this.nodes.find((n) => n.id === edge.from);
      const to = this.nodes.find((n) => n.id === edge.to);
      if (!from || !to) return "";
      const fs = this.sizes[from.id] && this.sizes[from.id].w > 0 ? this.sizes[from.id] : { w: 190, h: 96 };
      const ts = this.sizes[to.id] && this.sizes[to.id].w > 0 ? this.sizes[to.id] : { w: 190, h: 96 };
      const fromWait = from.role === "wait";
      const toWait = to.role === "wait";
      const a = { x: fromWait ? fs.w : from.x + fs.w, y: from.y + fs.h / 2 };
      const b = { x: toWait ? this.rightRail : to.x, y: to.y + ts.h / 2 };
      const dx = Math.max(50, Math.abs(b.x - a.x) * 0.5);
      return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x - 2} ${b.y}`;
    },
    linkPath() {
      if (!this.link) return "";
      const from = this.nodes.find((n) => n.id === this.link.from);
      if (!from) return "";
      const fs = this.sizes[from.id] && this.sizes[from.id].w > 0 ? this.sizes[from.id] : { w: 190, h: 96 };
      const a = { x: from.role === "wait" ? fs.w : from.x + fs.w, y: from.y + fs.h / 2 };
      const b = this.link;
      const dx = Math.max(50, Math.abs(b.x - a.x) * 0.5);
      return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
    },
    worldPoint(event) {
      const rect = this.$refs.canvas.getBoundingClientRect();
      return {
        x: (event.clientX - rect.left - this.view.x) / this.view.scale,
        y: (event.clientY - rect.top - this.view.y) / this.view.scale,
      };
    },
    startNodeDrag(node, event, copy) {
      if (this.readonly || event.button !== 0) return;
      this.selectedId = node.id;
      this.drag = { id: node.id, sx: event.clientX, sy: event.clientY, ox: node.x, oy: node.y, wait: node.role === "wait" };
    },
    startPan(event) {
      if (event.button !== 0) return;
      this.selectedEdgeId = "";
      this.pan = { sx: event.clientX, sy: event.clientY, ox: this.view.x, oy: this.view.y };
    },
    onMove(event) {
      if (this.drag) {
        const node = this.nodes.find((n) => n.id === this.drag.id);
        if (node) {
          if (!this.drag.wait) node.x = Math.round(this.drag.ox + (event.clientX - this.drag.sx) / this.view.scale);
          node.y = Math.round(this.drag.oy + (event.clientY - this.drag.sy) / this.view.scale);
        }
      } else if (this.pan) {
        this.view.x = this.pan.ox + event.clientX - this.pan.sx;
        this.view.y = this.pan.oy + event.clientY - this.pan.sy;
      } else if (this.link) {
        Object.assign(this.link, this.worldPoint(event));
      }
    },
    onUp() {
      if (this.link) this.link = null;
      this.drag = null;
      this.pan = null;
    },
    onWheel(event) {
      event.preventDefault();
      event.stopPropagation();
      const rect = this.$refs.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left, my = event.clientY - rect.top;
      const old = this.view.scale;
      const next = Math.max(0.35, Math.min(1.8, old * (event.deltaY < 0 ? 1.1 : 0.9)));
      this.view.x = mx - (mx - this.view.x) * (next / old);
      this.view.y = my - (my - this.view.y) * (next / old);
      this.view.scale = next;
    },
    startLink(node, event) {
      const p = this.worldPoint(event);
      this.link = { from: node.id, x: p.x, y: p.y };
    },
    onNodeUp(node) {
      if (this.link) this.finishLink(node);
      else this.onUp();
    },
    finishLink(node) {
      if (!this.link || this.link.from === node.id) { this.link = null; return; }
      const exists = this.edges.some((e) => e.from === this.link.from && e.to === node.id);
      if (!exists) {
        this.edges.push({ id: `e_${Date.now().toString(36)}`, from: this.link.from, to: node.id, label: "" });
      }
      this.link = null;
    },
    selectNode(node) {
      if (this.readonly) return;
      this.selectedId = node.id;
      this.selectedEdgeId = "";
    },
    selectEdge(edge) {
      if (this.readonly) return;
      this.selectedEdgeId = edge.id;
      this.selectedId = "";
    },
    onKeyDown(event) {
      if (this.readonly) return;
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const tag = (event.target?.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || event.target?.isContentEditable) return;
      if (this.selectedEdgeId) {
        event.preventDefault();
        this.deleteEdgeNow(this.selectedEdgeId);
      } else if (this.selectedId) {
        const node = this.nodes.find((n) => n.id === this.selectedId);
        if (node) { event.preventDefault(); this.deleteNodeNow(node); }
      }
    },
    mediaName(node) {
      const item = (this.library || []).find((x) => x.id === node?.media_id);
      if (item) return item.name;
      return String(node?.video || "").split("/").pop() || "未配置视频";
    },
    onMedia(id) {
      if (!this.selected) return;
      const item = (this.library || []).find((x) => x.id === id);
      this.selected.media_id = id;
      if (item) {
        this.selected.video = item.video;
        this.selected.duration = Number(item.duration) || 0;
      }
    },
    addNode() {
      const id = `node_${Date.now().toString(36)}`;
      const rect = this.$refs.canvas.getBoundingClientRect();
      const x = Math.round((rect.width / 2 - this.view.x) / this.view.scale - 95);
      const y = Math.round((rect.height / 2 - this.view.y) / this.view.scale - 48);
      this.nodes.push({ id, label: "新节点", video: "", loop: false, role: "action", prompt: "", note: "", duration: 0, media_id: "", x: Math.max(20, x), y: Math.max(20, y) });
      this.$nextTick(() => { this.measure(); this.selectedId = id; });
    },
    async deleteNode() {
      if (!this.selected) return;
      if (!(await this.confirm("删除节点", `删除节点「${this.selected.label}」？`, true))) return;
      await this.removeNode(this.selected);
    },
    async removeNode(node) {
      const loopRoles = ["wait", "both", "work", "phone", "rest"];
      const remaining = this.nodes.filter((n) => n.id !== node.id);
      if (!remaining.some((n) => loopRoles.includes(n.role))) {
        this.toast("画布至少要保留一个循环播放节点", "err");
        return;
      }
      this.nodes = remaining;
      this.edges = this.edges.filter((e) => e.from !== node.id && e.to !== node.id);
      this.selectedId = "";
      this.selectedEdgeId = "";
      await this.save(true);
    },
    async deleteNodeNow(node) {
      if (this.readonly || !node) return;
      await this.removeNode(node);
      this.toast(`已删除节点「${node.label}」`);
    },
    async deleteEdgeNow(edgeId) {
      if (this.readonly) return;
      this.edges = this.edges.filter((e) => e.id !== edgeId);
      this.selectedEdgeId = "";
      await this.save(true);
      this.toast("已删除连线");
    },
    async save(silent = false) {
      if (this.projectMode) {
        this.$emit("save-project", { nodes: this.nodes, edges: this.edges });
        if (!silent) this.toast("已提交保存");
        return true;
      }
      try {
        const data = await apiPost("flow", { action: "save", flow: { nodes: this.nodes, edges: this.edges } });
        this.$emit("flow-changed", data.flow);
        if (!silent) this.toast("画布已保存");
        return true;
      } catch (error) {
        this.toast(`保存失败：${error.message || error}`, "err");
        return false;
      }
    },
    async generate() {
      if (!(await this.confirm("重新生成画布", "用当前动作视频重新生成？现有连线和节点会被覆盖。"))) return;
      try {
        const data = await apiPost("flow", { action: "generate" });
        this.fitDone = false;
        this.loadFlow(data.flow);
        this.$emit("flow-changed", data.flow);
        this.toast("画布已重新生成");
      } catch (error) {
        this.toast(`生成失败：${error.message || error}`, "err");
      }
    },
    async play() {
      if (!this.selected) return;
      try {
        const data = await apiPost("flow", { action: "goto", node_id: this.selected.id });
        this.$emit("state-changed", data.state);
        this.toast(`已切到「${this.selected.label}」`);
      } catch (error) {
        this.toast(`切换失败：${error.message || error}`, "err");
      }
    },
  },
  template: `
    <section class="tab-page canvas-page" :class="{ preview: readonly }">
      <div class="page-head" v-if="!readonly">
        <div><h2>动作画布</h2><p class="muted">拖节点、从圆点拉线；点节点或连线选中后按 Delete，右键也可删除。</p></div>
        <div class="head-actions">
          <button class="btn" type="button" @click="addNode">新增节点</button>
          <button class="btn" type="button" @click="generate" v-if="!projectMode">重新生成</button>
          <button class="btn" type="button" @click="refit">适应画布</button>
          <button class="btn primary" type="button" @click="save()">保存画布</button>
        </div>
      </div>
      <div class="current-node"><i></i>当前节点：{{ currentLabel }}</div>
      <div class="canvas-layout" :class="{ 'has-editor': selected && !readonly }">
        <div class="canvas" ref="canvas" @mousedown.self="startPan" @wheel="onWheel">
          <div class="world" :style="worldStyle">
            <svg class="edges" width="4000" height="3000">
              <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#c8b79d"></path></marker></defs>
              <g v-for="edge in edges" :key="edge.id">
                <path :d="edgePath(edge)" fill="none" stroke="transparent" stroke-width="14" class="edge-hit" @click.stop="selectEdge(edge)" @contextmenu.prevent.stop="deleteEdgeNow(edge.id)"></path>
                <path :d="edgePath(edge)" fill="none" :stroke="edge.id === selectedEdgeId ? '#3f6f7e' : '#c8b79d'" :stroke-width="edge.id === selectedEdgeId ? 3 : 2" marker-end="url(#arrow)" pointer-events="none"></path>
              </g>
              <path v-if="link" :d="linkPath()" fill="none" stroke="#4f7d8c" stroke-width="2" stroke-dasharray="5 4"></path>
            </svg>
            <template v-for="node in nodes" :key="node.id">
              <div v-for="copy in nodeCopies(node)" :key="copy" :ref="el => setNodeRef(node.id, el)"
                class="fnode" :class="['role-' + (node.role || 'action'), { current: node.id === currentId, 'wait-left': node.role === 'wait' && copy === 'left', 'wait-right': node.role === 'wait' && copy === 'right' }]"
                :style="{ left: nodeLeft(node, copy) + 'px', top: node.y + 'px' }"
                @mousedown.stop="startNodeDrag(node, $event, copy)" @mouseup.stop.prevent="onNodeUp(node)" @click.stop="selectNode(node)" @contextmenu.prevent.stop="deleteNodeNow(node)">
                <span v-if="node.role === 'wait' && copy === 'left'" class="wait-side left">提前收到消息</span>
                <span v-if="node.role === 'wait' && copy === 'right'" class="wait-side right">完成任务</span>
                <div class="fnode-title">{{ node.label }}</div>
                <div class="fnode-meta">{{ roleLabel(node.role) }} · {{ node.loop ? '循环' : '播一次' }}<span v-if="mediaName(node) !== roleLabel(node.role)"> · {{ mediaName(node) }}</span></div>
                <div class="fnode-desc">{{ roleDescription(node.role) }}</div>
                <div v-if="node.prompt" class="fnode-prompt">{{ node.prompt }}</div>
                <template v-if="!readonly">
                  <span v-if="copy !== 'left'" class="port in"></span>
                  <span v-if="copy !== 'right'" class="port out" @mousedown.stop.prevent="startLink(node, $event)"></span>
                </template>
              </div>
            </template>
          </div>
        </div>
        <aside class="node-editor" v-if="selected && !readonly">
          <h3>节点设置</h3>
          <label>名称<input v-model="selected.label" maxlength="40" /></label>
          <label>类型
            <el-select v-model="selected.role" style="width: 100%" @change="onRoleChange">
              <el-option label="循环等待" value="wait"></el-option>
              <el-option label="回消息/工作" value="both"></el-option>
              <el-option label="仅工作" value="work"></el-option>
              <el-option label="仅回消息" value="phone"></el-option>
              <el-option label="动作" value="action"></el-option>
              <el-option label="过渡" value="transition"></el-option>
            </el-select>
          </label>
          <p class="role-desc">{{ roleDescription(selected.role) }}</p>
          <label>素材<el-select :model-value="selected.media_id" style="width: 100%" @change="onMedia">
            <el-option label="（不使用素材库）" value=""></el-option>
            <el-option v-for="item in library" :key="item.id" :label="item.name + ' · ' + Number(item.duration || 0).toFixed(1) + 's'" :value="item.id"></el-option>
          </el-select></label>
          <label>提示词<textarea v-model="selected.prompt" rows="3" maxlength="300"></textarea></label>
          <label>视频地址<input v-model="selected.video" placeholder="videos/xxx.mp4 或 http(s)://" /></label>
          <label>时长（秒）<input v-model.number="selected.duration" type="number" min="0" step="0.1" /></label>
          <div class="node-actions">
            <button class="btn small primary" type="button" @click="play" v-if="!projectMode">播放此节点</button>
            <button class="btn small" type="button" @click="save()">保存</button>
            <button class="btn small danger" type="button" @click="deleteNode" v-if="!readonly">删除</button>
          </div>
        </aside>
      </div>
    </section>`,
};
