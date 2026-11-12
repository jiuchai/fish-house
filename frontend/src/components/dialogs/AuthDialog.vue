<template>
  <div v-if="panel.auth.show" class="modal" @click.self="panel.auth.show = false">
    <div class="dialog auth-modal">
      <h3>主题市场账号</h3>
      <div class="auth-tabs">
        <button type="button" :class="{ active: panel.auth.mode === 'login' }" @click="panel.auth.mode = 'login'">登录</button>
        <button type="button" :class="{ active: panel.auth.mode === 'register' }" @click="panel.auth.mode = 'register'">注册</button>
      </div>
      <input v-model="panel.auth.email" v-if="panel.auth.mode === 'register'" type="email" placeholder="邮箱（唯一 ID）" autocomplete="email" />
      <input v-model="panel.auth.username" :placeholder="panel.auth.mode === 'login' ? '用户名或邮箱' : '用户名'" autocomplete="username" />
      <input v-model="panel.auth.password" type="password" placeholder="密码" autocomplete="current-password" @keyup.enter="submitAuth" />
      <p v-if="panel.auth.error" class="auth-error">{{ panel.auth.error }}</p>
      <div class="dialog-actions">
        <button class="btn" type="button" @click="panel.auth.show = false">取消</button>
        <button class="btn primary" type="button" :disabled="panel.auth.busy" @click="submitAuth">{{ panel.auth.mode === 'login' ? '登录' : '注册' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { panel, submitAuth } from "../../store.js";
</script>
