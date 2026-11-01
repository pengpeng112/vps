<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NCard, NInputNumber, NSelect, NSpace, NTable, NTag, useMessage } from 'naive-ui'
import { api } from '../api'

const message = useMessage()
const devices = ref<any[]>([])
const dev = ref<string | null>(null)
const hours = ref<number>(72)
const rows = ref<any[]>([])
const totalRx = ref(0)
const totalTx = ref(0)
const alertMb = ref<number>(0)
const loading = ref(false)

async function loadDevices() {
  const r = await api.get('/api/v1/devices?page_size=100')
  const list = r.items || r.data || r.devices || []
  devices.value = list.map((d: any) => ({ label: `${d.name || d.id}（${d.id}）`, value: d.id }))
  if (devices.value.length && !dev.value) dev.value = devices.value[0].value
}

async function load() {
  if (!dev.value) return
  loading.value = true
  try {
    const r = await api.get(`/api/v1/devices/${dev.value}/traffic/hourly?hours=${hours.value}`)
    rows.value = r.items || []
    totalRx.value = r.total_rx_mb || 0
    totalTx.value = r.total_tx_mb || 0
    alertMb.value = r.hourly_alert_mb || 0
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

async function saveThreshold() {
  if (!dev.value) return
  try {
    await api.patch(`/api/v1/devices/${dev.value}`, { traffic_alert_hourly_mb: alertMb.value || 0 })
    message.success('预警阈值已保存（0=关闭）')
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

const maxVal = computed(() => Math.max(1, ...rows.value.map(r => Math.max(r.rx_mb, r.tx_mb))))
const recent = computed(() => rows.value.slice(-24).reverse())

function barH(v: number) {
  return Math.max(2, Math.round((v / maxVal.value) * 110))
}
function fmt(n: number) {
  return n >= 1024 ? (n / 1024).toFixed(2) + ' GB' : n.toFixed(1) + ' MB'
}

onMounted(async () => {
  await loadDevices()
  load()
})
</script>

<template>
  <n-space vertical size="large">
    <n-card title="流量分析（分时段）">
      <template #header-extra>
        <n-space>
          <n-select v-model:value="dev" filterable size="small" style="width: 260px" :options="devices"
            placeholder="选择设备" @update:value="load" />
          <n-select v-model:value="hours" size="small" style="width: 110px" :options="[
            { label: '近24小时', value: 24 }, { label: '近3天', value: 72 },
            { label: '近7天', value: 168 }, { label: '近30天', value: 720 },
          ]" @update:value="load" />
          <n-button size="small" :loading="loading" @click="load">刷新</n-button>
        </n-space>
      </template>
      <n-space vertical size="small">
        <n-space align="center">
          <n-tag type="info" size="small">时段总下行：{{ fmt(totalRx) }}</n-tag>
          <n-tag type="success" size="small">时段总上行：{{ fmt(totalTx) }}</n-tag>
          <span style="font-size: 12px; color: #888">蓝=下行 绿=上行（每小时，Asia/Shanghai）</span>
        </n-space>
        <div style="display: flex; align-items: flex-end; gap: 2px; height: 120px; overflow-x: auto; padding: 4px 0">
          <div v-for="r in rows" :key="r.hour"
            :title="`${r.hour}  下行 ${fmt(r.rx_mb)} / 上行 ${fmt(r.tx_mb)} / 峰值 ${r.peak_mbps.toFixed(1)} Mbps`"
            style="display: flex; flex-direction: column; justify-content: flex-end; height: 110px; min-width: 9px">
            <div style="width: 9px; background: #63e2b7; border-radius: 1px 1px 0 0"
              :style="{ height: barH(r.tx_mb) + 'px' }"></div>
            <div style="width: 9px; background: #4098fc"
              :style="{ height: barH(r.rx_mb) + 'px' }"></div>
          </div>
          <span v-if="!rows.length && !loading" style="color: #888">该时段内暂无样本（设备离线或刚上线）</span>
        </div>
      </n-space>
    </n-card>

    <n-card title="高流量预警阈值（每设备，WAN 下行）">
      <n-space align="center">
        <span>最近 1 小时下行超过</span>
        <n-input-number v-model:value="alertMb" :min="0" :max="10000000" :step="256" style="width: 160px" />
        <span>MB 时告警（0=关闭；每小时最多提醒一次，进「告警」页）</span>
        <n-button type="primary" size="small" @click="saveThreshold">保存阈值</n-button>
      </n-space>
      <div style="font-size: 12px; color: #888; margin-top: 6px">
        保存后即时生效。需要限速时到「设备」页对该设备设置 vps_limit_kbps（走 set_ratelimit 通道）。
      </div>
    </n-card>

    <n-card title="逐小时明细（最近 24 桶）">
      <n-table size="small" :bordered="false">
        <thead>
          <tr><th>小时（CST）</th><th>下行</th><th>上行</th><th>峰值速率</th><th>样本数</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in recent" :key="r.hour">
            <td>{{ r.hour }}</td>
            <td>{{ fmt(r.rx_mb) }}</td>
            <td>{{ fmt(r.tx_mb) }}</td>
            <td>{{ r.peak_mbps.toFixed(1) }} Mbps</td>
            <td>{{ r.samples }}</td>
          </tr>
          <tr v-if="!recent.length">
            <td colspan="5" style="color: #888">暂无数据</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>
  </n-space>
</template>
