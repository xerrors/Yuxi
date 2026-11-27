<template>
  <div class="api-integration-panel settings-page">
    <div class="settings-header">
      <div class="settings-header-copy">
        <h2 class="settings-section-title">接入概览</h2>
      </div>
      <div class="settings-header-actions">
        <a-button
          class="yuxi-refresh-button lucide-icon-btn"
          :loading="loading"
          title="刷新接入概览"
          aria-label="刷新接入概览"
          @click="load"
        >
          <template #icon><RefreshCw :size="16" :class="{ 'yuxi-rotate': loading }" /></template>
        </a-button>
      </div>
    </div>

    <div class="settings-management-content">
      <a-spin :spinning="loading">
        <div v-if="error" class="settings-error">
          <a-alert type="error" :message="error" show-icon />
        </div>

        <div v-if="overview" class="metric-strip">
          <div class="metric-card">
            <span class="metric-label">接入 APP</span>
            <strong class="metric-value">{{ totals.app_count }}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">有效 API Key</span>
            <strong class="metric-value">{{ totals.key_count }}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">影子用户</span>
            <strong class="metric-value">{{ totals.end_user_count }}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">累计输入</span>
            <strong class="metric-value">{{ totals.input_count }}</strong>
          </div>
        </div>

        <section v-if="overview" class="integration-section">
          <h3 class="integration-section-title">APP 与授权</h3>
          <a-empty v-if="appRows.length === 0" :image="false" description="尚未创建绑定 APP 的 API Key" />
          <div v-else class="settings-table-wrapper">
            <a-table
              :dataSource="appRows"
              :columns="appColumns"
              :rowKey="(record) => record.app_id"
              :pagination="false"
              :scroll="{ x: 760 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'app_id'">
                  <div class="settings-table-title">
                    <AppWindow :size="15" class="settings-table-icon" />
                    <span class="settings-table-name" :title="record.app_id">{{ record.app_id }}</span>
                  </div>
                </template>
                <template v-if="column.key === 'keys'">
                  <a-tag v-for="key in record.keys" :key="key.id" :color="key.is_valid ? 'blue' : 'default'">
                    {{ key.name }} · {{ key.access_level }}{{ key.is_valid ? '' : ' · 无效' }}
                  </a-tag>
                </template>
              </template>
            </a-table>
          </div>
        </section>

        <section class="integration-section">
          <h3 class="integration-section-title">对外端点</h3>
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="endpoints"
              :columns="endpointColumns"
              :rowKey="(record) => `${record.method} ${record.path}`"
              :pagination="false"
              :scroll="{ x: 800 }"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'method'">
                  <a-tag :color="methodColor(record.method)">{{ record.method }}</a-tag>
                </template>
                <template v-if="column.key === 'path'">
                  <code class="code-badge">{{ record.path }}</code>
                </template>
              </template>
            </a-table>
          </div>
        </section>
      </a-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { AppWindow, RefreshCw } from '@lucide/vue'
import { apikeyApi, publicApiEndpoints } from '@/apis/apikey_api'
import { healthApi } from '@/apis/system_api'

const loading = ref(false)
const error = ref('')
const overview = ref(null)
const knowledgeEnabled = ref(false)
let loadSequence = 0
const endpoints = computed(() => publicApiEndpoints(knowledgeEnabled.value))

const totals = computed(() => overview.value?.totals || {})
const appRows = computed(() => overview.value?.apps || [])

const appColumns = [
  { title: 'APP ID', key: 'app_id', dataIndex: 'app_id' },
  { title: '已授权 Key', key: 'keys', width: 280 },
  { title: '影子用户', key: 'end_user_count', dataIndex: 'end_user_count', width: 110 },
  { title: '累计输入', key: 'input_count', dataIndex: 'input_count', width: 110 }
]

const endpointColumns = [
  { title: '方法', key: 'method', width: 90 },
  { title: '路径', key: 'path' },
  { title: '说明', key: 'description', dataIndex: 'description', width: 220 }
]

const methodColor = (method) => ({ GET: 'blue', POST: 'green', PUT: 'orange', DELETE: 'red' })[method] || 'default'

/** 读取 APP 概览，避免修改 Key 前发出的请求覆盖更新后的数据。 */
async function load() {
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  try {
    const data = await apikeyApi.listApps()
    if (sequence === loadSequence) overview.value = data
  } catch (e) {
    if (sequence !== loadSequence) return
    overview.value = null
    error.value = e?.message || '加载 API 集成信息失败'
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

/** 能力发现失败或未开启时保持隐藏，不把知识库端点先画出来。 */
async function loadKnowledgeCapability() {
  try {
    const data = await healthApi.getDiscovery()
    knowledgeEnabled.value = data?.capabilities?.features?.knowledge === true
  } catch (capabilityError) {
    knowledgeEnabled.value = false
    console.warn('加载系统能力失败，隐藏知识库公开端点:', capabilityError)
  }
}

onMounted(() => {
  void load()
  void loadKnowledgeCapability()
})

defineExpose({ loading, load })
</script>

<style lang="less" scoped>
.metric-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--border-surface);
  border-radius: 8px;
  background: var(--gray-0);
}

.metric-label {
  color: var(--gray-600);
  font-size: 12px;
}

.metric-value {
  color: var(--gray-1000);
  font-size: 20px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.integration-section {
  display: flex;
  flex-direction: column;
  gap: 8px;

  & + & {
    margin-top: 20px;
  }
}

.integration-section-title {
  margin: 0;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

:deep(.ant-table-thead > tr > th) {
  color: var(--gray-600);
}
</style>
