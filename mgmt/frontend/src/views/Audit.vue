<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NTable, NPagination, NButton, NSpace } from 'naive-ui'
import { api, fmtTime } from '../api'

const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

async function load() {
  const r = await api.get(`/api/v1/audit?page=${page.value}&page_size=${pageSize.value}`)
  items.value = r.items || []
  total.value = r.total || 0
}

onMounted(load)
</script>

<template>
  <n-card title="审计日志（append-only，无删除接口）">
    <template #header-extra>
      <n-button size="small" @click="load">刷新</n-button>
    </template>
    <n-table size="small" :bordered="false">
      <thead><tr><th>ID</th><th>时间</th><th>操作者</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
      <tbody>
        <tr v-for="l in items" :key="l.id">
          <td>{{ l.id }}</td>
          <td style="white-space: nowrap">{{ fmtTime(l.ts) }}</td>
          <td>{{ l.actor_type }}:{{ l.actor_id }}</td>
          <td><code>{{ l.action }}</code></td>
          <td><code>{{ l.target }}</code></td>
          <td style="max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ JSON.stringify(l.detail) }}</td>
          <td>{{ l.ip }}</td>
        </tr>
        <tr v-if="!items.length"><td colspan="7" style="color: #9aa0aa">暂无记录</td></tr>
      </tbody>
    </n-table>
    <template #footer>
      <n-space justify="end">
        <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
      </n-space>
    </template>
  </n-card>
</template>
