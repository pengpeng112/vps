<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NTable, NTag, NButton, NSpace, NSelect, NPagination, useMessage } from 'naive-ui'
import { api, fmtTime } from '../api'

const message = useMessage()
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const state = ref<string | null>(null)

async function load() {
  const q = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value) })
  if (state.value) q.set('state', state.value)
  const r = await api.get(`/api/v1/alerts?${q}`)
  items.value = r.items || []
  total.value = r.total || 0
}

async function ack(a: any) {
  try {
    await api.patch(`/api/v1/alerts/${a.id}`, {})
    message.success('已确认')
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

function levelType(level: string) {
  return level === 'critical' ? 'error' : level === 'warning' ? 'warning' : 'info'
}

onMounted(load)
</script>

<template>
  <n-card title="告警（离线/出口变化/连续回滚/流量阈值/回滚失败，自动去重）">
    <template #header-extra>
      <n-space>
        <n-select v-model:value="state" clearable size="small" style="width: 130px" :options="[
          { label: '未确认', value: 'active' }, { label: '已确认', value: 'acked' },
        ]" placeholder="全部" @update:value="() => { page = 1; load() }" />
        <n-button size="small" @click="load">刷新</n-button>
      </n-space>
    </template>
    <n-table size="small" :bordered="false">
      <thead><tr><th>ID</th><th>级别</th><th>类型</th><th>设备</th><th>内容</th><th>时间</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="a in items" :key="a.id">
          <td>{{ a.id }}</td>
          <td><n-tag :type="levelType(a.level)" size="small">{{ a.level }}</n-tag></td>
          <td>{{ a.kind }}</td>
          <td style="font-family: monospace">{{ a.device_id || '—' }}</td>
          <td>{{ a.message }}</td>
          <td style="white-space: nowrap">{{ fmtTime(a.created_at) }}</td>
          <td>{{ a.acked_at ? `已确认(${a.acked_by || ''})` : '未确认' }}</td>
          <td><n-button v-if="!a.acked_at" size="tiny" @click="ack(a)">确认</n-button></td>
        </tr>
        <tr v-if="!items.length"><td colspan="8" style="color: #9aa0aa">暂无告警</td></tr>
      </tbody>
    </n-table>
    <template #footer>
      <n-space justify="end">
        <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
      </n-space>
    </template>
  </n-card>
</template>
