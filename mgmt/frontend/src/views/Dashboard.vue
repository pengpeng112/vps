<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NGrid, NGi, NStatistic, NProgress, NTable, NTag, NSpace, NSpin } from 'naive-ui'
import { api, fmtBytes, fmtTime } from '../api'

const loading = ref(true)
const dash = ref<any>({})

function levelType(level: string) {
  return level === 'critical' ? 'error' : level === 'warning' ? 'warning' : 'info'
}
function healthType(h: string) {
  return h === 'up' ? 'success' : h === 'down' ? 'error' : 'default'
}

onMounted(async () => {
  try {
    dash.value = await api.get('/api/v1/dashboard')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical size="large">
      <n-grid :cols="4" :x-gap="12">
        <n-gi><n-card><n-statistic label="在线设备" :value="dash.devices?.online ?? 0" /></n-card></n-gi>
        <n-gi><n-card><n-statistic label="离线设备" :value="dash.devices?.offline ?? 0" /></n-card></n-gi>
        <n-gi><n-card><n-statistic label="待注册" :value="dash.devices?.pending ?? 0" /></n-card></n-gi>
        <n-gi><n-card>
          <n-statistic label="本月节点流量合计" :value="fmtBytes(dash.traffic?.total_proxy_bytes)" />
          <template #footer>
            <span style="font-size: 12px; color: #9aa0aa">
              {{ dash.traffic?.month }} · 设备侧统计/估算，不等同 VPS 账单 · 阈值 {{ dash.traffic?.threshold_gb }} GB/设备/月
            </span>
          </template>
        </n-card></n-gi>
      </n-grid>

      <n-card title="本月代理流量（设备侧统计/估算）—— 省钱目标监控">
        <n-space vertical v-if="dash.traffic?.items?.length">
          <div v-for="d in dash.traffic.items" :key="d.device_id" style="display: flex; align-items: center; gap: 12px">
            <span style="width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ d.name || d.device_id }}</span>
            <n-progress style="flex: 1" :percentage="Math.min(100, d.percent)" :color="d.over_limit ? '#e88080' : '#63e2b7'"
              :height="14" :show-indicator="false" processing />
            <span style="width: 110px; text-align: right; font-family: monospace" :style="{ color: d.over_limit ? '#e88080' : '#7fe7c4' }">
              {{ fmtBytes(d.proxy_bytes) }}
            </span>
            <n-tag v-if="d.over_limit" type="error" size="small">超阈值</n-tag>
          </div>
        </n-space>
        <span v-else style="color: #9aa0aa">暂无数据（设备心跳带 proxy 字段后展示；采集不完整显示 unavailable）</span>
      </n-card>

      <n-grid :cols="2" :x-gap="12">
        <n-gi>
          <n-card title="节点分布与健康">
            <n-table size="small" :bordered="false">
              <thead><tr><th>节点</th><th>地址</th><th>健康(TCP)</th><th>绑定</th><th>活动</th><th>启用</th></tr></thead>
              <tbody>
                <tr v-for="n in dash.nodes || []" :key="n.id">
                  <td>{{ n.name }}</td>
                  <td style="font-family: monospace">{{ n.host }}</td>
                  <td><n-tag :type="healthType(n.health_status)" size="small">{{ n.health_status }}</n-tag></td>
                  <td>{{ n.bound_devices }}</td>
                  <td>{{ n.active_devices }}</td>
                  <td>{{ n.enabled ? '是' : '否' }}</td>
                </tr>
                <tr v-if="!(dash.nodes || []).length"><td colspan="6" style="color:#9aa0aa">暂无节点</td></tr>
              </tbody>
            </n-table>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="最近告警">
            <n-table size="small" :bordered="false">
              <thead><tr><th>级别</th><th>内容</th><th>时间</th><th>状态</th></tr></thead>
              <tbody>
                <tr v-for="a in dash.recent_alerts || []" :key="a.id">
                  <td><n-tag :type="levelType(a.level)" size="small">{{ a.level }}</n-tag></td>
                  <td>{{ a.message }}</td>
                  <td style="white-space: nowrap">{{ fmtTime(a.created_at) }}</td>
                  <td>{{ a.acked ? '已确认' : '未确认' }}</td>
                </tr>
                <tr v-if="!(dash.recent_alerts || []).length"><td colspan="4" style="color:#9aa0aa">暂无告警</td></tr>
              </tbody>
            </n-table>
          </n-card>
        </n-gi>
      </n-grid>
    </n-space>
  </n-spin>
</template>
