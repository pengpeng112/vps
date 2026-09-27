<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NTable, NTag, NSpace, NSelect, NInput, NPagination } from 'naive-ui'
import { api, fmtTime } from '../api'

const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const status = ref<string | null>(null)
const deviceId = ref('')

async function load() {
  const q = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value) })
  if (status.value) q.set('status', status.value)
  if (deviceId.value.trim()) q.set('device_id', deviceId.value.trim())
  const r = await api.get(`/api/v1/tasks?${q}`)
  items.value = r.items || []
  total.value = r.total || 0
}

function taskStatusType(s: string) {
  return ({ ok: 'success', failed: 'error', rolled_back: 'warning', running: 'info', pending: 'default', expired: 'default', unknown: 'warning', cancelled: 'default' } as any)[s] || 'default'
}

onMounted(load)
</script>

<template>
  <n-card title="任务队列">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="deviceId" placeholder="按设备 ID 过滤" size="small" style="width: 180px" @keyup.enter="() => { page = 1; load() }" />
        <n-select v-model:value="status" clearable size="small" style="width: 130px" :options="[
          'pending', 'running', 'ok', 'failed', 'rolled_back', 'expired', 'unknown', 'cancelled',
        ].map((s) => ({ label: s, value: s }))" placeholder="状态" @update:value="() => { page = 1; load() }" />
        <n-button size="small" @click="load">刷新</n-button>
      </n-space>
    </template>
    <n-table size="small" :bordered="false">
      <thead><tr><th>任务</th><th>设备</th><th>类型</th><th>状态</th><th>参数</th><th>结果</th><th>创建人</th><th>创建</th><th>执行</th></tr></thead>
      <tbody>
        <tr v-for="t in items" :key="t.id">
          <td style="font-family: monospace">{{ t.id }}</td>
          <td style="font-family: monospace">{{ t.device_id }}</td>
          <td>{{ t.type }}</td>
          <td><n-tag :type="taskStatusType(t.status)" size="small">{{ t.status }}</n-tag></td>
          <td style="max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ JSON.stringify(t.params) }}</td>
          <td style="max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ t.result?.detail ? JSON.stringify(t.result.detail) : (t.result?.status || '—') }}
          </td>
          <td>{{ t.created_by }}</td>
          <td style="white-space: nowrap">{{ fmtTime(t.created_at) }}</td>
          <td style="white-space: nowrap">{{ fmtTime(t.executed_at) }}</td>
        </tr>
        <tr v-if="!items.length"><td colspan="9" style="color: #9aa0aa">暂无任务</td></tr>
      </tbody>
    </n-table>
    <template #footer>
      <n-space justify="end">
        <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
      </n-space>
    </template>
  </n-card>
</template>
