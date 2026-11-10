<template>
  <div class="api-integration-panel settings-page">
    <div class="settings-header">
      <div class="settings-header-actions">
        <a-button
          class="yuxi-refresh-button lucide-icon-btn"
          :loading="loading"
          title="刷新"
          @click="load"
        >
          <template #icon><RefreshCw :size="16" :class="{ 'yuxi-rotate': loading }" /></template>
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="openKeyManager">
          <Plus :size="14" />
          创建 API Key
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

        <section class="integration-section">
          <div class="integration-section-title">对外端点</div>
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

        <section v-if="overview" class="integration-section">
          <div class="integration-section-title">APP 与授权</div>
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
      </a-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { AppWindow, Plus, RefreshCw } from '@lucide/vue'
import { apikeyApi, publicApiEndpoints } from '@/apis/apikey_api'
import { healthApi } from '@/apis/system_api'

const settingsModal = inject('settingsModal', null)

const loading = ref(false)
const error = ref('')
const overview = ref(null)
const knowledgeEnabled = ref(false)
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

async function load() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await apikeyApi.listApps()
  } catch (e) {
    overview.value = null
    error.value = e?.message || '加载 API 集成信息失败'
  } finally {
    loading.value = false
  }
}

const openKeyManager = () => settingsModal?.openSettingsModal?.('apiKeys')

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

defineExpose({ loading, stats: totals })
</script>

<style lang="less" scoped>
.settings-header {
  justify-content: flex-end;
}

.api-integration-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 12px var(--page-padding) 24px;
}

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
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

:deep(.ant-table-thead > tr > th) {
  color: var(--gray-600);
}
</style>
