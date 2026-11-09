window.ThemeCard = {
  props: { item: Object, market: Boolean, showActions: { type: Boolean, default: true } },
  emits: ["apply", "remove", "like", "favorite", "edit", "update"],
  template: `
    <article class="theme-card">
      <div class="theme-cover">
        <img v-if="item.cover_url" :src="item.cover_url" alt="" loading="lazy" />
        <span v-else class="theme-fallback">鱼</span>
        <span v-if="market && item.mine" class="theme-own">我的</span>
      </div>
      <div class="theme-body">
        <b>{{ item.name }}</b>
        <small>{{ item.author || '匿名' }} · v{{ item.version || '1.0.0' }}<span v-if="item.size"> · {{ (item.size / 1048576).toFixed(1) }}MB</span></small>
        <p>{{ item.description || '没有介绍' }}</p>
        <div class="theme-stats" v-if="market">
          <button class="stat-btn like" :class="{ on: item.liked }" type="button" title="点赞" @click.stop="$emit('like', item)">
            <span class="stat-ico">♥</span><b>{{ item.likes || 0 }}</b>
          </button>
          <button class="stat-btn fav" :class="{ on: item.favorited }" type="button" title="收藏" @click.stop="$emit('favorite', item)">
            <span class="stat-ico">{{ item.favorited ? '★' : '☆' }}</span>
          </button>
          <span class="stat-item" title="下载">
            <span class="stat-ico">↓</span><b>{{ item.downloads || 0 }}</b>
          </span>
        </div>
      </div>
      <div class="theme-actions" v-if="showActions">
        <button v-if="!market" class="btn small" type="button" @click="$emit('edit', item.id)">编辑</button>
        <button v-if="market && item.mine" class="btn small" type="button" @click="$emit('edit', item)">编辑</button>
        <button v-if="market && item.mine" class="btn small" type="button" @click="$emit('update', item)">更新</button>
        <button class="btn small primary" type="button" @click="$emit('apply', item.id)">{{ market ? '下载并应用' : '应用' }}</button>
        <button v-if="!market || item.mine" class="btn small danger" type="button" @click="$emit('remove', item.id)">删除</button>
      </div>
    </article>`,
};
