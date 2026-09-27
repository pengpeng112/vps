<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  NCard, NTable, NTag, NButton, NSpace, NModal, NForm, NFormItem, NInput, NInputNumber,
  NSwitch, NPagination, useMessage,
} from 'naive-ui'
import { api, fmtTime } from '../api'

const message = useMessage()
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const showForm = ref(false)
const editId = ref<number | null>(null)
const form = ref<any>({ name: '', host: '', port: 443, priority: 0, enabled: true, params: { uuid: '', pbk: '', sid: '', sni: 'www.microsoft.com', flow: 'xtls-rprx-vision', fp: 'chrome' } })

async function load() {
  const r = await api.get(`/api/v1/nodes?page=${page.value}&page_size=${pageSize.value}`)
  items.value = r.items || []
  total.value = r.total || 0
}

function openCreate() {
  editId.value = null
  form.value = { name: '', host: '', port: 443, priority: 0, enabled: true, params: { uuid: '', pbk: '', sid: '', sni: 'www.microsoft.com', flow: 'xtls-rprx-vision', fp: 'chrome' } }
  showForm.value = true
}

function openEdit(n: any) {
  editId.value = n.id
  form.value = { name: n.name, host: n.host, port: n.port, priority: n.priority, enabled: n.enabled, params: { sni: n.params.sni, flow: n.params.flow, fp: n.params.fp } }
  showForm.value = true
}

async function save() {
  try {
    if (editId.value) {
      const body: any = { name: form.value.name, host: form.value.host, port: form.value.port, priority: form.value.priority, enabled: form.value.enabled }
      const p: any = {}
      if (form.value.params.uuid) p.uuid = form.value.params.uuid
      if (form.value.params.pbk) p.pbk = form.value.params.pbk
      if (form.value.params.sid) p.sid = form.value.params.sid
      if (form.value.params.sni) p.sni = form.value.params.sni
      body.params = p
      await api.patch(`/api/v1/nodes/${editId.value}`, body)
    } else {
      await api.post('/api/v1/nodes', form.value)
    }
    showForm.value = false
    message.success('已保存（凭据加密存储；列表仅显示掩码）')
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function removeNode(n: any) {
  if (!confirm(`确认删除节点 ${n.name}？被绑定时会被拒绝。`)) return
  try {
    await api.delete(`/api/v1/nodes/${n.id}`)
    message.success('已删除')
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function probe(n: any) {
  try {
    const r = await api.post(`/api/v1/nodes/${n.id}/probe`)
    message.info(`${n.name}: ${r.health_status}${r.latency_ms != null ? ` (${r.latency_ms}ms)` : ''} —— TCP 可达≠Reality 鉴权可用`)
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

function healthType(h: string) {
  return h === 'up' ? 'success' : h === 'down' ? 'error' : 'default'
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large">
    <n-card title="出口节点（VLESS+Reality；参数加密存储，界面只显示掩码）">
      <template #header-extra>
        <n-button type="primary" @click="openCreate">创建节点</n-button>
      </template>
      <n-table size="small" :bordered="false">
        <thead>
          <tr><th>ID</th><th>名称</th><th>地址</th><th>uuid</th><th>pbk</th><th>sid</th><th>健康(TCP)</th><th>绑定</th><th>启用</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="n in items" :key="n.id">
            <td>{{ n.id }}</td>
            <td>{{ n.name }}</td>
            <td style="font-family: monospace">{{ n.host }}:{{ n.port }}</td>
            <td style="font-family: monospace">{{ n.params?.uuid }}</td>
            <td style="font-family: monospace">{{ n.params?.pbk }}</td>
            <td style="font-family: monospace">{{ n.params?.sid }}</td>
            <td>
              <n-tag :type="healthType(n.health_status)" size="small">{{ n.health_status }}</n-tag>
              <span v-if="n.last_latency_ms != null" style="margin-left: 4px; font-size: 12px">{{ n.last_latency_ms }}ms</span>
            </td>
            <td>{{ n.bound_devices }}</td>
            <td>{{ n.enabled ? '是' : '否' }}</td>
            <td>
              <n-space size="small">
                <n-button size="tiny" @click="probe(n)">探测</n-button>
                <n-button size="tiny" @click="openEdit(n)">编辑</n-button>
                <n-button size="tiny" type="error" @click="removeNode(n)">删除</n-button>
              </n-space>
            </td>
          </tr>
          <tr v-if="!items.length"><td colspan="10" style="color: #9aa0aa">暂无节点（Session 1 默认录入虚构测试节点；现网参数从保密文档人工导入）</td></tr>
        </tbody>
      </n-table>
      <template #footer>
        <n-space justify="end">
          <n-pagination v-model:page="page" :page-size="pageSize" :item-count="total" @update:page="load" />
        </n-space>
      </template>
    </n-card>

    <n-modal v-model:show="showForm" preset="card" :title="editId ? '编辑节点（留空=保留原值；掩码不可写回）' : '创建节点'" style="width: 560px">
      <n-form label-placement="left" label-width="110">
        <n-form-item label="名称"><n-input v-model:value="form.name" placeholder="字母数字-_(2~32)" /></n-form-item>
        <n-form-item label="IP (IPv4)"><n-input v-model:value="form.host" placeholder="203.0.113.10（Session 1 仅 IPv4 字面量）" /></n-form-item>
        <n-form-item label="端口"><n-input-number v-model:value="form.port" :min="1" :max="65535" /></n-form-item>
        <n-form-item label="UUID" v-if="!editId"><n-input v-model:value="form.params.uuid" placeholder="11111111-2222-3333-4444-555555555555" /></n-form-item>
        <n-form-item label="Reality pbk" v-if="!editId"><n-input v-model:value="form.params.pbk" placeholder="43 字符 base64url" /></n-form-item>
        <n-form-item label="short-id" v-if="!editId"><n-input v-model:value="form.params.sid" placeholder="偶数长度 hex" /></n-form-item>
        <n-form-item label="SNI"><n-input v-model:value="form.params.sni" /></n-form-item>
        <n-form-item label="flow"><n-input v-model:value="form.params.flow" /></n-form-item>
        <n-form-item label="指纹"><n-input v-model:value="form.params.fp" /></n-form-item>
        <n-form-item label="优先级"><n-input-number v-model:value="form.priority" /></n-form-item>
        <n-form-item label="启用"><n-switch v-model:value="form.enabled" /></n-form-item>
        <n-button type="primary" block @click="save">保存</n-button>
      </n-form>
    </n-modal>
  </n-space>
</template>
