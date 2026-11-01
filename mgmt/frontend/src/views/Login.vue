<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, NSpace, NAlert, useMessage } from 'naive-ui'
import { api, setToken } from '../api'

const router = useRouter()
const message = useMessage()

const username = ref('')
const password = ref('')
const code = ref('')
const ticket = ref('')
const stage = ref<'pwd' | 'totp' | 'setup'>('pwd')
const setupSecret = ref('')
const otpauthUrl = ref('')
const loading = ref(false)

async function login() {
  if (loading.value) return
  loading.value = true
  try {
    const r = await api.post('/api/v1/auth/login', { username: username.value, password: password.value })
    ticket.value = r.ticket
    if (r.need_setup) {
      const s = await api.post('/api/v1/auth/totp/setup', { ticket: ticket.value })
      setupSecret.value = s.secret
      otpauthUrl.value = s.otpauth_url
      stage.value = 'setup'
      message.info('首次登录需绑定 TOTP（扫码或手输密钥）')
    } else {
      stage.value = 'totp'
    }
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

async function submitCode() {
  if (loading.value) return
  loading.value = true
  try {
    const path = stage.value === 'setup' ? '/api/v1/auth/totp/confirm' : '/api/v1/auth/totp'
    const r = await api.post(path, { ticket: ticket.value, code: code.value })
    setToken(r.token)
    message.success('登录成功')
    router.push('/')
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div style="display: flex; align-items: center; justify-content: center; height: 100vh; background: #101014">
    <n-card title="路由器管理台 · 登录" style="width: 400px" embedded>
      <n-space vertical size="large">
        <template v-if="stage === 'pwd'">
          <!-- 2026-10-02：autocomplete + form submit（浏览器保存密码提示；与 R3 LuCI 同方案） -->
          <!-- 2026-10-10：去掉 @keyup.enter（与 form submit 双触发 login()）；submit 按钮在 form 内回车原生提交 -->
          <n-form @submit.prevent="login">
            <n-form-item label="用户名">
              <n-input v-model:value="username" :input-props="{ autocomplete: 'username', name: 'username' }" placeholder="管理员用户名" />
            </n-form-item>
            <n-form-item label="密码">
              <n-input v-model:value="password" type="password" show-password-on="click" :input-props="{ autocomplete: 'current-password', name: 'password' }" placeholder="密码" />
            </n-form-item>
            <n-button attr-type="submit" type="primary" block :loading="loading">登录</n-button>
          </n-form>
        </template>

        <template v-else-if="stage === 'setup'">
          <n-alert type="info">
            首次登录：请用验证器 App 扫描或手动输入密钥绑定 TOTP。
            <div style="margin-top: 8px; font-size: 12px; color: #9aa0aa">otpauth: {{ otpauthUrl.slice(0, 72) }}...</div>
            <div style="margin-top: 6px; font-family: monospace; color: #7fe7c4">密钥：{{ setupSecret }}</div>
          </n-alert>
          <n-form @keyup.enter="submitCode">
            <n-form-item label="6 位验证码">
              <n-input v-model:value="code" :input-props="{ autocomplete: 'one-time-code', name: 'code' }" placeholder="输入验证器当前验证码" />
            </n-form-item>
            <n-button type="primary" block :loading="loading" @click="submitCode">绑定并登录</n-button>
          </n-form>
        </template>

        <template v-else>
          <n-alert type="info">输入 TOTP 验证码完成登录</n-alert>
          <n-form @keyup.enter="submitCode">
            <n-form-item label="6 位验证码">
              <n-input v-model:value="code" :input-props="{ autocomplete: 'one-time-code', name: 'code' }" placeholder="TOTP 验证码" />
            </n-form-item>
            <n-button type="primary" block :loading="loading" @click="submitCode">登录</n-button>
          </n-form>
        </template>
      </n-space>
    </n-card>
  </div>
</template>
