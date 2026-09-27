<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NCard, NSpace, NTag, NTable, NButton, NModal, NForm, NFormItem, NInput, NInputNumber,
  NSelect, NGrid, NGi, NTabs, NTabPane, NSpin, NDescriptions, NDescriptionsItem, NAlert,
  NRadioGroup, NRadioButton, useMessage,
} from 'naive-ui'
import * as echarts from 'echarts'
import { api, fmtBytes, fmtTime, fmtDaysLeft, toLocalInput } from '../api'

const route = useRoute()
const message = useMessage()
const id = String(route.params.id)
const detail = ref<any>({ device: {}, bound_nodes: [], latest_metric: {} })
const metrics = ref<any[]>([])
const range = ref<'1h' | '24h' | '7d'>('24h')
const nodes = ref<any[]>([])
const tasks = ref<any[]>([])
const loading = ref(true)

const previewData = ref<any>(null)
const showPreview = ref(false)

// 操作
const opsBusy = ref(false)
const pingHost = ref('223.5.5.5')
const bindNodeId = ref<number | null>(null)

const rateKbps = ref(0)
const rebootCron = ref('')
const proxyMode = ref<'rule' | 'global' | 'direct'>('rule')
const accessMode = ref<'all' | 'whitelist' | 'blacklist'>('all')
const accessList = ref('')
const firstOnlineLocal = ref('')
const licenseStartLocal = ref('')
const licenseExpireLocal = ref('')
const termSaving = ref(false)
const showDiag = ref(false)
const diagText = ref('')
const rebootOptions = [
  { label: '关闭定时重启', value: '' },
  { label: '每天 0 点', value: '0 0 * * *' },
  { label: '每天 8 点', value: '0 8 * * *' },
  { label: '每 8 小时', value: '0 */8 * * *' },
  { label: '每 12 小时', value: '0 */12 * * *' },
]
const rateOptions = [
  { label: '不限速', value: 0 },
  { label: '1 Mbps', value: 1024 },
  { label: '5 Mbps', value: 5120 },
  { label: '10 Mbps', value: 10240 },
  { label: '20 Mbps', value: 20480 },
  { label: '50 Mbps', value: 51200 },
]

// 改名 + VPS 停用/启用
const nameEdit = ref('')
const nameSaving = ref(false)
// 心跳指标里 exit_node=DIRECT 即代理已被远程停用（60s 心跳粒度）
const proxyDisabled = computed(() => (detail.value.latest_metric?.exit_node ?? '') === 'DIRECT')

const cpuChart = ref<HTMLElement>()
const trafficChart = ref<HTMLElement>()
const latencyChart = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

function fmtMS(ms?: number | null) { return ms == null ? '—' : `${ms} ms` }

async function loadDetail() {
  detail.value = await api.get(`/api/v1/devices/${id}`)
  nameEdit.value = detail.value.device?.name || ''
  rateKbps.value = detail.value.device?.vps_limit_kbps ?? 0
  rebootCron.value = detail.value.device?.reboot_cron ?? ''
  proxyMode.value = detail.value.device?.proxy_mode || 'rule'
  accessMode.value = detail.value.device?.access_mode || 'all'
  accessList.value = (detail.value.device?.access_list || '').split(',').filter(Boolean).join('\n')
  firstOnlineLocal.value = toLocalInput(detail.value.device?.first_online_at)
  licenseStartLocal.value = toLocalInput(detail.value.device?.license_started_at)
  licenseExpireLocal.value = toLocalInput(detail.value.device?.license_expires_at)
}

async function saveName() {
  const n = nameEdit.value.trim()
  if (!n) { message.warning('名称不能为空'); return }
  if (n === detail.value.device?.name) return
  nameSaving.value = true
  try {
    await api.patch(`/api/v1/devices/${id}`, { name: n })
    message.success('名称已保存')
    loadDetail()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    nameSaving.value = false
  }
}

async function toggleProxy() {
  const target = !proxyDisabled.value // true=要停用
  if (target && !confirm('停用后该设备所有流量将直连（不走 VPS），确认停用？')) return
  await doOps('set_proxy_state', { enabled: !target })
  setTimeout(loadDetail, 6500) // 等一轮心跳反映 exit_node
}

async function upgradeAgent() {
  if (!confirm('远程升级该设备的 Agent（签名校验+失败自动保留备份），升级后服务自动重启，确认？')) return
  await doOps('agent_update', {}) // 不填版本=服务器最新包
}

async function toggleWifi(on: boolean) {
  if (!on && !confirm('远程关闭该设备 Wi-Fi？300 秒内未再下发"开启"将自动恢复（管理通道不受影响）。')) return
  await doOps('set_wifi', { enabled: on })
}

async function loadMetrics() {
  const r = await api.get(`/api/v1/devices/${id}/metrics?range=${range.value}`)
  metrics.value = r.items || []
  renderCharts()
}

async function loadTasks() {
  const r = await api.get(`/api/v1/tasks?device_id=${id}&page_size=20`)
  tasks.value = r.items || []
}

function renderCharts() {
  charts.forEach((c) => c.dispose())
  charts = []
  const dark = { textStyle: { color: '#9aa0aa' } }
  if (cpuChart.value) {
    const c = echarts.init(cpuChart.value, 'dark')
    c.setOption({
      ...dark, grid: { left: 50, right: 10, top: 20, bottom: 24 },
      xAxis: { type: 'category', data: metrics.value.map((m) => fmtTime(m.ts).slice(11)) },
      yAxis: [{ type: 'value', max: 100, name: 'CPU%' }, { type: 'value', name: 'MB' }],
      legend: { data: ['CPU%', '内存'], textStyle: { color: '#9aa0aa' } },
      series: [
        { name: 'CPU%', type: 'line', showSymbol: false, data: metrics.value.map((m) => m.cpu ?? null), smooth: true },
        { name: '内存', type: 'line', showSymbol: false, yAxisIndex: 1, data: metrics.value.map((m) => m.mem_used_mb ?? null), smooth: true },
      ],
    })
    charts.push(c)
  }
  if (trafficChart.value) {
    const c = echarts.init(trafficChart.value, 'dark')
    c.setOption({
      ...dark, grid: { left: 70, right: 10, top: 30, bottom: 24 },
      xAxis: { type: 'category', data: metrics.value.map((m) => fmtTime(m.ts).slice(11)) },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => fmtBytes(v) } },
      legend: { data: ['代理下载(估)', '代理上传(估)', 'WAN 下载', 'WAN 上传'], textStyle: { color: '#9aa0aa' } },
      series: [
        { name: '代理下载(估)', type: 'line', showSymbol: false, areaStyle: {}, smooth: true, data: metrics.value.map((m) => parseInt(m.proxy_rx_bytes ?? '0')) },
        { name: '代理上传(估)', type: 'line', showSymbol: false, smooth: true, data: metrics.value.map((m) => parseInt(m.proxy_tx_bytes ?? '0')) },
        { name: 'WAN 下载', type: 'line', showSymbol: false, smooth: true, data: metrics.value.map((m) => parseInt(m.wan_rx_bytes ?? '0')) },
        { name: 'WAN 上传', type: 'line', showSymbol: false, smooth: true, data: metrics.value.map((m) => parseInt(m.wan_tx_bytes ?? '0')) },
      ],
    })
    charts.push(c)
  }
  if (latencyChart.value) {
    const c = echarts.init(latencyChart.value, 'dark')
    c.setOption({
      ...dark, grid: { left: 50, right: 10, top: 20, bottom: 24 },
      xAxis: { type: 'category', data: metrics.value.map((m) => fmtTime(m.ts).slice(11)) },
      yAxis: { type: 'value', name: 'ms' },
      series: [{ type: 'line', showSymbol: false, smooth: true, data: metrics.value.map((m) => m.exit_latency_ms ?? null) }],
    })
    charts.push(c)
  }
}

watch(range, loadMetrics)

async function loadNodes() {
  const r = await api.get('/api/v1/nodes?page_size=100')
  nodes.value = (r.items || []).map((n: any) => ({ label: `${n.name} (${n.host})`, value: n.id, enabled: n.enabled }))
}

async function doOps(type: string, params: Record<string, unknown> = {}) {
  opsBusy.value = true
  try {
    const key = `${type}-${Date.now()}`
    const r = await api.post(`/api/v1/devices/${id}/ops`, { type, params }, { 'Idempotency-Key': key })
    message.success(`任务 ${r.task_id} 已入队（${type}）`)
    setTimeout(loadTasks, 1000)
    return r.task_id as string
  } catch (e: any) {
    message.error(e.message)
    return ''
  } finally {
    opsBusy.value = false
  }
}

async function applyRate() {
  await doOps('set_ratelimit', { kbps: Number(rateKbps.value) || 0 })
  setTimeout(loadDetail, 1500)
}
async function applyProxyMode() {
  await doOps('set_proxy_mode', { mode: proxyMode.value })
  setTimeout(loadDetail, 1500)
}
async function applyAccessMode() {
  await doOps('set_access_mode', { mode: accessMode.value, list: accessList.value })
  setTimeout(loadDetail, 1500)
}
async function applyRebootCron() {
  await doOps('set_reboot_schedule', { cron: rebootCron.value || '' })
  setTimeout(loadDetail, 1500)
}
async function saveTerm() {
  termSaving.value = true
  try {
    const body: Record<string, unknown> = {}
    if (firstOnlineLocal.value) body.first_online_at = new Date(firstOnlineLocal.value).toISOString()
    if (licenseStartLocal.value) body.license_started_at = new Date(licenseStartLocal.value).toISOString()
    body.license_expires_at = licenseExpireLocal.value ? new Date(licenseExpireLocal.value).toISOString() : ''
    await api.patch(`/api/v1/devices/${id}`, body)
    message.success('上线时间与期限已保存')
    await loadDetail()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    termSaving.value = false
  }
}

async function applyLicense(kind: 'month' | 'year' | 'forever' | 'disable') {
  const params: Record<string, unknown> =
    kind === 'month' ? { days: 30 } :
    kind === 'year' ? { days: 365 } :
    kind === 'forever' ? { enabled: true, expires_at: '' } :
    { enabled: false }
  if (kind === 'disable' && !confirm('停用后该设备不能再走 VPS（国内直连仍可用），确认？')) return
  await doOps('set_license', params)
  setTimeout(loadDetail, 1500)
}
async function collectDiag() {
  const tid = await doOps('collect_diag')
  showDiag.value = true
  diagText.value = '等待设备上报诊断（约 10–20 秒）…'
  if (!tid) return
  for (let i = 0; i < 15; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    await loadTasks()
    const t = tasks.value.find((x: any) => x.id === tid) || tasks.value.find((x: any) => x.type === 'collect_diag')
    if (t?.status === 'ok' || t?.status === 'failed') {
      diagText.value = JSON.stringify(t.result?.detail || t.result || t, null, 2)
      return
    }
  }
  diagText.value = '超时未返回。设备若离线，等它上线后在下方任务历史里看 collect_diag 结果。'
}

async function bindNode(apply: boolean) {
  if (!bindNodeId.value) {
    message.warning('先选择节点')
    return
  }
  try {
    const r = await api.post('/api/v1/assignments/batch', {
      device_ids: [id], node_id: bindNodeId.value, is_primary: true, apply,
    })
    const item = r.items?.[0]
    if (item?.ok) {
      message.success(apply ? `已发布并生成任务 ${item.task_id}` : '绑定已更新（未发布配置）')
    } else {
      message.error(item?.error || '绑定失败')
    }
    loadDetail(); loadTasks()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function previewConfig() {
  try {
    previewData.value = await api.post(`/api/v1/devices/${id}/configs/preview`, {})
    showPreview.value = true
  } catch (e: any) {
    message.error(e.message)
  }
}

async function applyConfig() {
  if (!previewData.value) return
  try {
    const r = await api.post(`/api/v1/devices/${id}/configs/apply`, { revision: previewData.value.revision },
      { 'Idempotency-Key': `apply-${Date.now()}` })
    message.success(`已发布 v${r.version}，任务 ${r.task_id}`)
    showPreview.value = false
    loadDetail(); loadTasks()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function revoke() {
  if (!confirm('确认吊销该设备？管理凭据立即作废，未执行任务取消。')) return
  try {
    await api.post(`/api/v1/devices/${id}/revoke`)
    message.warning('设备已吊销')
    loadDetail()
  } catch (e: any) {
    message.error(e.message)
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadDetail(), loadMetrics(), loadTasks(), loadNodes()])
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
  window.addEventListener('resize', renderCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', renderCharts)
  charts.forEach((c) => c.dispose())
})

function taskStatusType(s: string) {
  return ({ ok: 'success', failed: 'error', rolled_back: 'warning', running: 'info', pending: 'default', expired: 'default', unknown: 'warning', cancelled: 'default' } as any)[s] || 'default'
}
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical size="large">
      <n-card :title="`设备 ${detail.device?.name || id}`">
        <template #header-extra>
          <n-space>
            <n-tag :type="detail.device?.online ? 'success' : 'warning'">{{ detail.device?.online ? '在线' : '离线' }}</n-tag>
            <n-tag v-if="proxyDisabled" type="warning">VPS 已停用</n-tag>
            <n-tag v-if="detail.device && detail.device.service_active === false" type="error">授权停用</n-tag>
            <n-tag type="default">{{ detail.device?.lifecycle_status }}</n-tag>
          </n-space>
        </template>
        <n-descriptions :column="4" size="small" label-placement="left" bordered>
          <n-descriptions-item label="ID">{{ detail.device?.id }}</n-descriptions-item>
          <n-descriptions-item label="WG 地址">{{ detail.device?.wg_ip }}</n-descriptions-item>
          <n-descriptions-item label="型号">{{ detail.device?.hw_model || '—' }}</n-descriptions-item>
          <n-descriptions-item label="固件">{{ detail.device?.firmware_ver || '—' }}</n-descriptions-item>
          <n-descriptions-item label="出口 IP">{{ detail.device?.current_exit_ip || '—' }}</n-descriptions-item>
          <n-descriptions-item label="期望主节点">{{ detail.device?.current_node_name || '—' }}</n-descriptions-item>
          <n-descriptions-item label="版本(生效/期望/最高接受)">
            {{ detail.device?.current_config_version }} / {{ detail.device?.desired_config_version }} / {{ detail.device?.highest_accepted_version }}
          </n-descriptions-item>
          <n-descriptions-item label="上线时间">{{ fmtTime(detail.device?.first_online_at) }}</n-descriptions-item>
          <n-descriptions-item label="期限起算">{{ fmtTime(detail.device?.license_started_at) }}</n-descriptions-item>
          <n-descriptions-item label="最后心跳">{{ fmtTime(detail.device?.last_heartbeat_at) }}</n-descriptions-item>
          <n-descriptions-item label="排障SSH(-R)">
            <span v-if="detail.device?.remote_ssh_alive === null || detail.device?.remote_ssh_alive === undefined" style="color:#9aa0aa">未探测</span>
            <n-tag v-else :type="detail.device.remote_ssh_alive ? 'success' : 'error'" size="small">
              {{ detail.device.remote_ssh_alive ? '通' : '断' }}
            </n-tag>
            <span style="color:#9aa0aa;font-size:12px"> 检测于 {{ fmtTime(detail.device?.last_remote_ssh_check_at) }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="VPS 限速">{{ detail.device?.vps_limit_kbps ? (detail.device.vps_limit_kbps + ' kbps') : '不限' }}</n-descriptions-item>
          <n-descriptions-item label="定时重启">{{ rebootOptions.find(o => o.value === (detail.device?.reboot_cron || ''))?.label || '关闭' }}</n-descriptions-item>
          <n-descriptions-item label="授权到期">{{ detail.device?.license_expires_at ? fmtTime(detail.device.license_expires_at) : '永久' }}</n-descriptions-item>
          <n-descriptions-item label="剩余天数">{{ fmtDaysLeft(detail.device?.license_days_left, detail.device?.license_expires_at) }}</n-descriptions-item>
          <n-descriptions-item label="授权开关">{{ detail.device?.license_enabled === false ? '已关闭' : '开启' }}</n-descriptions-item>
          <n-descriptions-item label="运行模式">{{ ({ rule: '策略分流', global: '全局代理', direct: '全部直连' } as any)[detail.device?.proxy_mode] || '策略分流' }}</n-descriptions-item>
          <n-descriptions-item label="访问模式">{{ ({ all: '全部设备', whitelist: '白名单', blacklist: '黑名单' } as any)[detail.device?.access_mode] || '全部设备' }}</n-descriptions-item>
        </n-descriptions>
        <n-alert v-if="detail.device && detail.device.service_active === false" type="error" style="margin-top: 12px">
          该设备授权已停用或到期：VPS 通道被关掉，国内网站仍可直连。点下方「续期一个月/一年」或「永久」即可重新打开。
        </n-alert>
        <template #action>
          <n-space size="small">
            <n-input v-model:value="nameEdit" size="small" placeholder="设备名称" style="width: 160px" @keyup.enter="saveName" />
            <n-button size="small" :loading="nameSaving" @click="saveName">改名</n-button>
            <n-select v-model:value="bindNodeId" :options="nodes" placeholder="选择节点" style="width: 220px" />
            <n-button size="small" @click="bindNode(false)">绑定为主节点</n-button>
            <n-button size="small" type="primary" @click="bindNode(true)">绑定并发布配置</n-button>
            <n-button size="small" @click="previewConfig">配置预览/发布</n-button>
            <n-button size="small" @click="doOps('reload_proxy')">重载代理</n-button>
            <n-button size="small" :type="proxyDisabled ? 'success' : 'warning'" :loading="opsBusy" @click="toggleProxy">
              {{ proxyDisabled ? '启用 VPS' : '停用 VPS' }}
            </n-button>
            <n-input v-model:value="pingHost" size="small" placeholder="ping 目标" style="width: 130px" />
            <n-button size="small" :loading="opsBusy" @click="doOps('ping_test', { host: pingHost })">Ping 测试</n-button>
            <n-button size="small" type="warning" @click="doOps('reboot')">重启设备</n-button>
            <n-button size="small" :loading="opsBusy" @click="upgradeAgent">升级Agent</n-button>
            <n-button size="small" :loading="opsBusy" @click="toggleWifi(false)">关WiFi</n-button>
            <n-button size="small" :loading="opsBusy" @click="toggleWifi(true)">开WiFi</n-button>
            <n-button size="small" @click="collectDiag">拉取诊断</n-button>
            <n-button size="small" type="error" @click="revoke">吊销</n-button>
          </n-space>
        </template>
      </n-card>

      <n-card title="上线时间与使用期限（可手改）">
        <n-space vertical>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">上线时间</span>
            <input type="datetime-local" v-model="firstOnlineLocal" style="background:#16161c;color:#eee;border:1px solid #333;padding:4px 8px;border-radius:4px" />
            <span style="color:#9aa0aa;font-size:12px">设备第一次心跳自动写入，之后可改</span>
          </n-space>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">期限起算</span>
            <input type="datetime-local" v-model="licenseStartLocal" style="background:#16161c;color:#eee;border:1px solid #333;padding:4px 8px;border-radius:4px" />
          </n-space>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">到期时间</span>
            <input type="datetime-local" v-model="licenseExpireLocal" style="background:#16161c;color:#eee;border:1px solid #333;padding:4px 8px;border-radius:4px" />
            <n-button size="tiny" @click="licenseExpireLocal = ''">清空=永久</n-button>
            <n-button size="small" type="primary" :loading="termSaving" @click="saveTerm">保存时间</n-button>
          </n-space>
        </n-space>
      </n-card>

      <n-card title="运行模式 / 访问模式">
        <n-space vertical>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">运行模式</span>
            <n-radio-group v-model:value="proxyMode" name="proxyMode">
              <n-radio-button value="rule">策略分流</n-radio-button>
              <n-radio-button value="global">全局代理</n-radio-button>
              <n-radio-button value="direct">全部直连</n-radio-button>
            </n-radio-group>
            <n-button size="small" type="primary" :loading="opsBusy" @click="applyProxyMode">应用运行模式</n-button>
          </n-space>
          <div style="color:#9aa0aa;font-size:12px;padding-left:90px">
            策略分流：国内直连、境外走 VPS。全局代理：全部走 VPS。全部直连：不走 VPS。
          </div>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">访问模式</span>
            <n-radio-group v-model:value="accessMode" name="accessMode">
              <n-radio-button value="all">全部设备</n-radio-button>
              <n-radio-button value="whitelist">白名单</n-radio-button>
              <n-radio-button value="blacklist">黑名单</n-radio-button>
            </n-radio-group>
            <n-button size="small" type="primary" :loading="opsBusy" @click="applyAccessMode">应用访问模式</n-button>
          </n-space>
          <n-form-item v-if="accessMode !== 'all'" label="名单（每行一个局域网 IP 或 MAC）" style="max-width: 560px">
            <n-input v-model:value="accessList" type="textarea" :rows="4"
              placeholder="例如：&#10;192.168.1.10&#10;192.168.1.20&#10;04:d9:c8:69:be:07" />
          </n-form-item>
          <div style="color:#9aa0aa;font-size:12px;padding-left:90px">
            白名单：只有名单里的设备走上面的运行模式，其余直连。黑名单：名单里的设备强制直连，其余仍走运行模式。
          </div>
        </n-space>
      </n-card>

      <n-card title="策略：限速 / 定时重启 / 授权">
        <n-space vertical>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">VPS 限速</span>
            <n-select v-model:value="rateKbps" :options="rateOptions" style="width: 160px" />
            <n-input-number v-model:value="rateKbps" :min="0" :max="1000000" :step="256" style="width: 140px" />
            <span style="color: #9aa0aa; font-size: 12px">kbps，只限制走 VPS 的流量，国内直连不限</span>
            <n-button size="small" type="primary" :loading="opsBusy" @click="applyRate">应用限速</n-button>
          </n-space>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">定时重启</span>
            <n-select v-model:value="rebootCron" :options="rebootOptions" style="width: 200px" />
            <n-button size="small" type="primary" :loading="opsBusy" @click="applyRebootCron">应用重启计划</n-button>
          </n-space>
          <n-space align="center">
            <span style="width: 90px; color: #9aa0aa">授权</span>
            <n-button size="small" :loading="opsBusy" @click="applyLicense('month')">续期一个月</n-button>
            <n-button size="small" :loading="opsBusy" @click="applyLicense('year')">续期一年</n-button>
            <n-button size="small" :loading="opsBusy" @click="applyLicense('forever')">永久有效</n-button>
            <n-button size="small" type="warning" :loading="opsBusy" @click="applyLicense('disable')">立即停用</n-button>
          </n-space>
        </n-space>
      </n-card>

      <n-grid :cols="2" :x-gap="12">
        <n-gi><n-card title="CPU / 内存"><div ref="cpuChart" style="height: 240px"></div></n-card></n-gi>
        <n-gi><n-card title="流量（本月口径见仪表盘说明）"><div ref="trafficChart" style="height: 240px"></div></n-card></n-gi>
      </n-grid>
      <n-card title="出口延迟">
        <n-space style="margin-bottom: 8px">
          <n-select v-model:value="range" :options="[
            { label: '1 小时', value: '1h' }, { label: '24 小时', value: '24h' }, { label: '7 天', value: '7d' },
          ]" style="width: 120px" />
          <span v-if="detail.latest_metric?.proxy_measurement_status === 'partial'" style="color:#e2a642;font-size:12px">
            代理流量为估算（轮询采集，可能漏短连接）
          </span>
          <span v-else-if="detail.latest_metric?.proxy_measurement_status === 'unavailable'" style="color:#9aa0aa;font-size:12px">
            代理流量采集不可用（暂无数据/采集不完整）
          </span>
        </n-space>
        <div ref="latencyChart" style="height: 200px"></div>
      </n-card>

      <n-card title="任务历史">
        <n-table size="small" :bordered="false">
          <thead><tr><th>任务</th><th>类型</th><th>状态</th><th>结果</th><th>创建</th><th>执行</th></tr></thead>
          <tbody>
            <tr v-for="t in tasks" :key="t.id">
              <td style="font-family: monospace">{{ t.id }}</td>
              <td>{{ t.type }}</td>
              <td><n-tag :type="taskStatusType(t.status)" size="small">{{ t.status }}</n-tag></td>
              <td style="max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                {{ t.result?.detail ? JSON.stringify(t.result.detail) : (t.result?.status || '—') }}
              </td>
              <td style="white-space: nowrap">{{ fmtTime(t.created_at) }}</td>
              <td style="white-space: nowrap">{{ fmtTime(t.executed_at) }}</td>
            </tr>
            <tr v-if="!tasks.length"><td colspan="6" style="color:#9aa0aa">暂无任务</td></tr>
          </tbody>
        </n-table>
      </n-card>

      <n-modal v-model:show="showDiag" preset="card" title="设备诊断" style="width: 760px">
        <pre style="max-height: 480px; overflow: auto; font-size: 12px; background: #16161c; padding: 12px; border-radius: 6px; white-space: pre-wrap">{{ diagText }}</pre>
      </n-modal>
      <n-modal v-model:show="showPreview" preset="card" title="配置预览（掩码）与发布" style="width: 860px">
        <n-alert v-if="previewData?.diff?.length" type="info" style="margin-bottom: 10px">
          与当前版本 diff：{{ previewData.diff.length }} 行变化（- 旧 / + 新）
        </n-alert>
        <n-tabs default-value="yaml" type="line">
          <n-tab-pane name="yaml" tab="掩码 YAML">
            <pre style="max-height: 420px; overflow: auto; font-size: 12px; background: #16161c; padding: 12px; border-radius: 6px">{{ previewData?.masked_yaml }}</pre>
          </n-tab-pane>
          <n-tab-pane name="diff" tab="Diff">
            <pre style="max-height: 420px; overflow: auto; font-size: 12px; background: #16161c; padding: 12px; border-radius: 6px"><template v-for="(l, i) in previewData?.diff || []" :key="i"><span :style="{ color: l.op === '+' ? '#63e2b7' : '#e88080' }">{{ l.op }} {{ l.line }}</span>
</template></pre>
          </n-tab-pane>
        </n-tabs>
        <template #footer>
          <n-space justify="space-between">
            <span style="color: #9aa0aa">发布将生成不可变版本并签名下发（Agent 验签+健康检查+失败自动回滚）</span>
            <n-button type="primary" @click="applyConfig">确认发布 v{{ previewData?.next_version }}</n-button>
          </n-space>
        </template>
      </n-modal>
    </n-space>
  </n-spin>
</template>
