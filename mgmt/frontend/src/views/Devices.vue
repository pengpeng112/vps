<script setup lang="ts">
import { onMounted, ref, h } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NTable, NButton, NSpace, NTag, NModal, NForm, NFormItem, NInput, NAlert,
  NPagination, useMessage, NSelect,
} from 'naive-ui'
import { api, fmtTime, fmtDaysLeft } from '../api'

const router = useRouter()
const message = useMessage()
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const statusFilter = ref('')

const showCreate = ref(false)
const createForm = ref({ name: '', location_note: '' })
const enrollInfo = ref<any>(null)

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value) })
    if (statusFilter.value) q.set('status', statusFilter.value)
    const r = await api.get(`/api/v1/devices?${q}`)
    items.value = r.items || []
    total.value = r.total || 0
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

async function createDevice() {
  try {
    const r = await api.post('/api/v1/devices', createForm.value)
    enrollInfo.value = r
    showCreate.value = false
    message.success(`设备 ${r.id} 已创建；注册码仅显示一次`)
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

function copyEnroll() {
  if (enrollInfo.value) {
    navigator.clipboard?.writeText(enrollInfo.value.enroll_code)
    message.success('已复制注册码')
  }
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large">
    <n-card title="设备列表">
      <template #header-extra>
        <n-space>
          <n-select v-model:value="statusFilter" :options="[
            { label: '全部', value: '' }, { label: '在线', value: 'online' }, { label: '离线', value: 'offline' }, { label: '待注册', value: 'pending' }, { label: '已吊销', value: 'revoked' },
          ]" style="width: 110px" @update:value="() => { page = 1; load() }" />
          <n-button type="primary" @click="showCreate = true">创建设备</n-button>
        </n-space>
      </template>
      <n-table size="small" :bordered="false">
        <thead>
          <tr>
            <th>名称</th><th>ID</th><th>地点</th><th>状态</th><th>排障SSH</th><th>出口 IP</th>
            <th>上线时间</th><th>期限</th><th>最后心跳</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in items" :key="d.id" style="cursor: pointer" @click="router.push(`/devices/${d.id}`)">
            <td>{{ d.name }}</td>
            <td style="font-family: monospace">{{ d.id }}</td>
            <td>{{ d.location_note || '—' }}</td>
            <td>
              <n-tag :type="d.lifecycle_status === 'revoked' ? 'error' : d.service_active === false ? 'error' : d.online ? 'success' : 'warning'" size="small">
                {{ d.lifecycle_status === 'revoked' ? '已吊销' : d.service_active === false ? '授权停用' : d.online ? '在线' : '离线' }}
              </n-tag>
            </td>
            <td>
              <n-tag v-if="d.remote_ssh_alive === null || d.remote_ssh_alive === undefined" size="small" type="default">未探测</n-tag>
              <n-tag v-else :type="d.remote_ssh_alive ? 'success' : 'error'" size="small">
                {{ d.remote_ssh_alive ? '通' : '断' }}
              </n-tag>
            </td>
            <td style="font-family: monospace">{{ d.current_exit_ip || '—' }}</td>
            <td style="white-space: nowrap">{{ fmtTime(d.first_online_at) }}</td>
            <td style="white-space: nowrap">{{ fmtDaysLeft(d.license_days_left, d.license_expires_at) }}</td>
            <td style="white-space: nowrap">{{ fmtTime(d.last_heartbeat_at) }}</td>
            <td><n-button size="tiny" @click.stop="router.push(`/devices/${d.id}`)">详情</n-button></td>
          </tr>
          <tr v-if="!items.length && !loading"><td colspan="10" style="color: #9aa0aa">暂无设备</td></tr>
        </tbody>
      </n-table>
      <template #footer>
        <n-space justify="space-between">
          <span style="color: #9aa0aa">共 {{ total }} 台</span>
          <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
        </n-space>
      </template>
    </n-card>

    <n-modal v-model:show="showCreate" preset="card" title="创建设备" style="width: 460px">
      <n-form>
        <n-form-item label="名称"><n-input v-model:value="createForm.name" placeholder="如 家-客厅" /></n-form-item>
        <n-form-item label="地点备注"><n-input v-model:value="createForm.location_note" placeholder="可选" /></n-form-item>
        <n-button type="primary" block @click="createDevice">创建并生成注册码</n-button>
      </n-form>
    </n-modal>

    <n-modal :show="!!enrollInfo" preset="card" title="一次性注册信息（仅此一次显示）" style="width: 640px"
      @close="enrollInfo = null">
      <n-alert type="warning" style="margin-bottom: 12px">
        enroll_code 30 分钟内有效，用 provision CLI 代设备 enroll 后作废；关闭窗口前请保存。
      </n-alert>
      <n-table size="small" :bordered="false">
        <tbody>
          <tr><td style="width: 130px">设备 ID</td><td style="font-family: monospace">{{ enrollInfo?.id }}</td></tr>
          <tr><td>WG 地址</td><td style="font-family: monospace">{{ enrollInfo?.wg_ip }}</td></tr>
          <tr><td>注册码</td><td style="font-family: monospace; word-break: break-all">{{ enrollInfo?.enroll_code }}</td></tr>
        </tbody>
      </n-table>
      <n-space style="margin-top: 12px">
        <n-button size="small" @click="copyEnroll">复制注册码</n-button>
        <n-button size="small" type="primary" @click="enrollInfo = null">我已保存，关闭</n-button>
      </n-space>
    </n-modal>
  </n-space>
</template>
