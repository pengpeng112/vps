<script setup lang="ts">
import { h, ref, Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN,
  NLayout, NLayoutSider, NLayoutHeader, NLayoutContent, NMenu, NButton, NGlobalStyle,
} from 'naive-ui'
import {
  SpeedometerOutline, HardwareChipOutline, ServerOutline, ListOutline,
  AlertCircleOutline, DocumentTextOutline, SettingsOutline, StatsChartOutline,
} from '@vicons/ionicons5'
import { RouterLink } from 'vue-router'

const router = useRouter()
const route = useRoute()
const collapsed = ref(false)

function icon(ic: Component) {
  return () => h(ic)
}

const menuOptions = [
  { label: () => h(RouterLink, { to: { path: '/' } }, { default: () => '仪表盘' }), key: '/', icon: icon(SpeedometerOutline) },
  { label: () => h(RouterLink, { to: { path: '/devices' } }, { default: () => '设备' }), key: '/devices', icon: icon(HardwareChipOutline) },
  { label: () => h(RouterLink, { to: { path: '/nodes' } }, { default: () => '节点' }), key: '/nodes', icon: icon(ServerOutline) },
  { label: () => h(RouterLink, { to: { path: '/traffic' } }, { default: () => '流量分析' }), key: '/traffic', icon: icon(StatsChartOutline) },
  { label: () => h(RouterLink, { to: { path: '/tasks' } }, { default: () => '任务' }), key: '/tasks', icon: icon(ListOutline) },
  { label: () => h(RouterLink, { to: { path: '/alerts' } }, { default: () => '告警' }), key: '/alerts', icon: icon(AlertCircleOutline) },
  { label: () => h(RouterLink, { to: { path: '/audit' } }, { default: () => '审计' }), key: '/audit', icon: icon(DocumentTextOutline) },
  { label: () => h(RouterLink, { to: { path: '/settings' } }, { default: () => '设置' }), key: '/settings', icon: icon(SettingsOutline) },
]

function activeKey() {
  if (route.path.startsWith('/devices')) return '/devices'
  return route.path
}

function logout() {
  localStorage.removeItem('token')
  router.push('/login')
}
</script>

<template>
  <n-config-provider :theme="darkTheme" :locale="zhCN" :date-locale="dateZhCN">
    <n-global-style />
    <n-message-provider>
      <n-dialog-provider>
        <div v-if="route.path === '/login'">
          <router-view />
        </div>
        <n-layout v-else has-sider style="height: 100vh">
          <n-layout-sider bordered collapse-mode="width" :collapsed-width="64" :width="200" :collapsed="collapsed"
            show-trigger @collapse="collapsed = true" @expand="collapsed = false">
            <div style="padding: 18px 16px; font-weight: 700; font-size: 15px; color: #7fe7c4" v-if="!collapsed">
              路由器管理台
            </div>
            <n-menu :collapsed="collapsed" :collapsed-width="64" :options="menuOptions" :value="activeKey()" />
          </n-layout-sider>
          <n-layout>
            <n-layout-header bordered style="height: 48px; display: flex; align-items: center; justify-content: space-between; padding: 0 20px">
              <span style="color: #9aa0aa">路由器集群后端管理系统</span>
              <n-button quaternary size="small" @click="logout">退出登录</n-button>
            </n-layout-header>
            <n-layout-content content-style="padding: 20px; min-height: calc(100vh - 48px); overflow: auto">
              <router-view />
            </n-layout-content>
          </n-layout>
        </n-layout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
