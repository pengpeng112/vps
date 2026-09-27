<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NForm, NFormItem, NInput, NButton, NSpace, NAlert, useMessage, NTag } from 'naive-ui'
import { api, clearToken } from '../api'

const message = useMessage()
const me = ref<any>({})
const pwd = ref({ old_password: '', new_password: '', totp_code: '' })
const meta = ref<any>({})

async function load() {
  me.value = await api.get('/api/v1/auth/me')
  meta.value = await api.get('/api/v1/meta')
}

async function changePwd() {
  try {
    await api.post('/api/v1/auth/password', pwd.value)
    message.success('密码已修改，旧会话全部失效；请重新登录')
    setTimeout(() => { clearToken(); location.href = '/login' }, 1500)
  } catch (e: any) {
    message.error(e.message)
  }
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large">
    <n-card title="账号">
      <n-space vertical>
        <div>用户名：<b>{{ me.username }}</b>　角色：<n-tag size="small">{{ me.role }}</n-tag>　TOTP：{{ me.totp_bound ? '已绑定' : '未绑定' }}</div>
        <n-alert type="info">
          TOTP 丢失时通过宿主机本地 CLI 验证运维身份后重绑（见 README）；管理台不提供绕过入口。
          传输模式：<b>{{ meta.transport_mode }}</b>{{ meta.transport_mode === 'mock' ? '（Session 1 无真 WG，仅开发直连）' : '' }}
        </n-alert>
      </n-space>
    </n-card>

    <n-card title="修改密码（需旧密码 + TOTP；修改后所有会话失效）" style="max-width: 560px">
      <n-form label-placement="left" label-width="110">
        <n-form-item label="旧密码"><n-input v-model:value="pwd.old_password" type="password" show-password-on="click" /></n-form-item>
        <n-form-item label="新密码"><n-input v-model:value="pwd.new_password" type="password" placeholder="至少 10 位" show-password-on="click" /></n-form-item>
        <n-form-item label="TOTP 验证码"><n-input v-model:value="pwd.totp_code" placeholder="6 位验证码" /></n-form-item>
        <n-button type="primary" @click="changePwd">修改密码</n-button>
      </n-form>
    </n-card>
  </n-space>
</template>
