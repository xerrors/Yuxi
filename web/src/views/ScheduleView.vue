<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Plus, RefreshCw, Play, Edit3, Trash2, FileText, ExternalLink, Info } from 'lucide-vue-next'

import { scheduleApi, agentApi } from '@/apis'
import { useAgentStore } from '@/stores/agent'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import CronSelector from '@/components/shared/CronSelector.vue'

// ============ State ============
const router = useRouter()
const agentStore = useAgentStore()

const loading = ref(false)
const listData = ref([])
const searchQuery = ref('')

// Pagination
const pagination = reactive({
  current: 1,
  pageSize: 15,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['10', '15', '20', '50']
})

// Agent Config Options
const agentConfigOptions = ref([])
const agentConfigMap = ref({}) // config_id -> display_name

// Modal for Create/Edit
const isModalVisible = ref(false)
const isEditing = ref(false)
const currentScheduleId = ref(null)
const modalSaving = ref(false)

const formRef = ref(null)
const formState = reactive({
  name: '',
  description: '',
  agent_config_id: null,
  cron_expr: '',
  timezone: 'Asia/Shanghai',
  query: '',
  config_text: '{}',
  enabled: true
})

const rules = {
  name: [{ required: true, message: '请输入任务名称', trigger: 'blur' }],
  agent_config_id: [{ required: true, message: '请选择绑定的智能体配置', trigger: 'change' }],
  cron_expr: [{ required: true, message: '请配置定时周期', trigger: 'change' }],
  query: [{ required: true, message: '请输入触发 Query 提示词', trigger: 'blur' }]
}

// Drawer for Logs
const isDrawerVisible = ref(false)
const currentLogSchedule = ref(null)
const logsLoading = ref(false)
const logsData = ref([])
const logsPagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0
})

// ============ Computed ============
const filteredListData = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return listData.value

  return listData.value.filter(
    (item) =>
      item.name.toLowerCase().includes(query) ||
      (item.description && item.description.toLowerCase().includes(query)) ||
      item.cron_expr.toLowerCase().includes(query)
  )
})

// ============ Methods ============
const formatDateTime = (value) => {
  if (!value) return '-'
  try {
    const date = new Date(value)
    // 检查是否是有效日期
    if (isNaN(date.getTime())) return '-'

    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${hh}:${mm}:${ss}`
  } catch {
    return '-'
  }
}

// Load Agent Configs
const loadAgentConfigs = async () => {
  try {
    if (agentStore.agents.length === 0) {
      await agentStore.fetchAgents()
    }

    const options = []
    const tempMap = {}

    await Promise.all(
      agentStore.agents.map(async (agent) => {
        try {
          const res = await agentApi.getAgentConfigs(agent.id)
          const configs = res.configs || []
          configs.forEach((cfg) => {
            const label = `${agent.name || agent.id} - ${cfg.name}`
            options.push({
              value: cfg.id,
              label: label
            })
            tempMap[cfg.id] = label
          })
        } catch (e) {
          console.warn(`获取智能体 ${agent.id} 的配置失败:`, e)
        }
      })
    )

    agentConfigOptions.value = options
    agentConfigMap.value = tempMap
  } catch (error) {
    console.error('加载智能体列表失败:', error)
  }
}

// Load Schedule List
const loadSchedules = async () => {
  loading.value = true
  try {
    const res = await scheduleApi.list({
      limit: pagination.pageSize,
      offset: (pagination.current - 1) * pagination.pageSize
    })

    if (res && res.success) {
      listData.value = res.data || []
      // 模拟总数，因为接口无 total，我们在这里处理一下
      pagination.total = res.data ? res.data.length + (pagination.current > 1 ? 15 : 0) : 0
    }
  } catch (error) {
    console.error('加载定时任务列表失败:', error)
    message.error('加载定时任务列表失败')
  } finally {
    loading.value = false
  }
}

const handleTableChange = (pag) => {
  pagination.current = pag.current
  pagination.pageSize = pag.pageSize
  loadSchedules()
}

// Toggle Enabled Status
const handleToggleEnabled = async (record) => {
  const oldVal = record.enabled
  const newVal = !oldVal

  try {
    const res = await scheduleApi.update(record.id, { enabled: newVal })
    if (res && res.success) {
      message.success(`${newVal ? '启用' : '禁用'}成功`)
      await loadSchedules()
    } else {
      record.enabled = oldVal // 还原
    }
  } catch (error) {
    console.error('切换启用状态失败:', error)
    message.error('操作失败')
  }
}

// Delete Schedule
const handleDelete = (record) => {
  Modal.confirm({
    title: '删除定时任务',
    content: `您确定要删除定时任务 "${record.name}" 吗？该操作不可恢复。`,
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const res = await scheduleApi.delete(record.id)
        if (res && res.success) {
          message.success('删除成功')
          await loadSchedules()
        }
      } catch (error) {
        console.error('删除定时任务失败:', error)
        message.error('删除失败')
      }
    }
  })
}

// Trigger Manually
const handleTrigger = (record) => {
  Modal.confirm({
    title: '立即执行',
    content: `确定要立即为定时任务 "${record.name}" 触发一次对话运行吗？这将在后台异步启动一个独立的会话。`,
    okText: '立即执行',
    cancelText: '取消',
    async onOk() {
      try {
        const res = await scheduleApi.trigger(record.id)
        if (res && res.success) {
          message.success('触发运行成功，已在后台启动对话')
          // 重新拉取以更新最近触发时间
          await loadSchedules()
        }
      } catch (error) {
        console.error('触发定时任务失败:', error)
        message.error('触发运行失败')
      }
    }
  })
}

// Show Create Modal
const showCreateModal = () => {
  isEditing.value = false
  currentScheduleId.value = null

  Object.assign(formState, {
    name: '',
    description: '',
    agent_config_id: agentConfigOptions.value[0]?.value || null,
    cron_expr: '0 9 * * 1-5', // 默认每个工作日早上9点
    timezone: 'Asia/Shanghai',
    query: '请播报今天的新闻与重要事项',
    config_text: '{}',
    enabled: true
  })

  isModalVisible.value = true
}

// Show Edit Modal
const showEditModal = (record) => {
  isEditing.value = true
  currentScheduleId.value = record.id

  let configText
  try {
    configText = JSON.stringify(record.config || {}, null, 2)
  } catch {
    configText = '{}'
  }

  Object.assign(formState, {
    name: record.name,
    description: record.description || '',
    agent_config_id: record.agent_config_id,
    cron_expr: record.cron_expr,
    timezone: record.timezone || 'Asia/Shanghai',
    query: record.query,
    config_text: configText,
    enabled: record.enabled
  })

  isModalVisible.value = true
}

// Save Schedule Form
const handleSave = async () => {
  if (!formRef.value) return

  try {
    await formRef.value.validate()

    // Parse config JSON
    let parsedConfig = {}
    try {
      parsedConfig = JSON.parse(formState.config_text || '{}')
      if (typeof parsedConfig !== 'object' || Array.isArray(parsedConfig)) {
        throw new Error('必须为 JSON 对象')
      }
    } catch {
      message.error('高级配置不是有效的 JSON 格式')
      return
    }

    const payload = {
      name: formState.name,
      description: formState.description || null,
      agent_config_id: formState.agent_config_id,
      cron_expr: formState.cron_expr,
      timezone: formState.timezone,
      query: formState.query,
      config: parsedConfig,
      enabled: formState.enabled
    }

    modalSaving.value = true
    let success = false

    if (isEditing.value) {
      const res = await scheduleApi.update(currentScheduleId.value, payload)
      if (res && res.success) {
        message.success('更新定时任务成功')
        success = true
      }
    } else {
      const res = await scheduleApi.create(payload)
      if (res && res.success) {
        message.success('创建定时任务成功')
        success = true
      }
    }

    if (success) {
      isModalVisible.value = false
      await loadSchedules()
    }
  } catch (error) {
    console.error('保存表单失败:', error)
    if (error.errorFields) {
      // 表单校验失败，不需要额外弹错
      return
    }
    message.error(error.message || '保存失败')
  } finally {
    modalSaving.value = false
  }
}

// Show Log Drawer
const showLogs = async (record) => {
  currentLogSchedule.value = record
  logsPagination.current = 1
  logsPagination.total = 0
  logsData.value = []
  isDrawerVisible.value = true
  await loadLogs()
}

// Load Logs
const loadLogs = async () => {
  if (!currentLogSchedule.value) return
  logsLoading.value = true
  try {
    const res = await scheduleApi.listLogs(currentLogSchedule.value.id, {
      limit: logsPagination.pageSize,
      offset: (logsPagination.current - 1) * logsPagination.pageSize
    })
    if (res && res.success) {
      logsData.value = res.data || []
      // 模拟日志数量
      logsPagination.total = res.data ? res.data.length + (logsPagination.current > 1 ? 10 : 0) : 0
    }
  } catch (error) {
    console.error('加载执行日志失败:', error)
    message.error('加载执行日志失败')
  } finally {
    logsLoading.value = false
  }
}

const handleLogsTableChange = (pag) => {
  logsPagination.current = pag.current
  loadLogs()
}

// Navigate to chat thread
const navigateToThread = (threadId) => {
  if (!threadId) return
  isDrawerVisible.value = false // 关闭抽屉
  router.push(`/agent/${threadId}`)
}

// Helper badge color for schedule log
const getTriggerStatusColor = (status) => {
  if (status === 'triggered') return 'success'
  return 'error'
}

const getTriggerStatusText = (status) => {
  if (status === 'triggered') return '启动成功'
  return '启动失败'
}

const getRunStatusColor = (status) => {
  switch (status) {
    case 'completed':
      return 'success'
    case 'running':
      return 'processing'
    case 'pending':
      return 'warning'
    case 'failed':
      return 'error'
    default:
      return 'default'
  }
}

const getRunStatusText = (status) => {
  switch (status) {
    case 'completed':
      return '已完成'
    case 'running':
      return '运行中'
    case 'pending':
      return '等待中'
    case 'failed':
      return '已失败'
    default:
      return status || '-'
  }
}

// ============ Table Columns ============
const columns = [
  {
    title: '任务名称',
    dataIndex: 'name',
    key: 'name',
    width: 220
  },
  {
    title: '绑定的智能体配置',
    dataIndex: 'agent_config_id',
    key: 'agent_config_id',
    width: 260
  },
  {
    title: '定时周期 (Cron)',
    dataIndex: 'cron_expr',
    key: 'cron_expr',
    width: 200
  },
  {
    title: '状态',
    dataIndex: 'enabled',
    key: 'enabled',
    width: 100
  },
  {
    title: '最近执行时间',
    dataIndex: 'last_run_at',
    key: 'last_run_at',
    width: 180
  },
  {
    title: '下次预计执行',
    dataIndex: 'next_run_at',
    key: 'next_run_at',
    width: 180
  },
  {
    title: '操作',
    key: 'actions',
    fixed: 'right',
    width: 260
  }
]

const logsColumns = [
  {
    title: '触发时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 180
  },
  {
    title: '触发状态',
    dataIndex: 'status',
    key: 'status',
    width: 110
  },
  {
    title: '运行状态',
    dataIndex: 'run_status',
    key: 'run_status',
    width: 100
  },
  {
    title: '执行细节 / 错误',
    dataIndex: 'error',
    key: 'error'
  },
  {
    title: '会话',
    key: 'actions',
    width: 100
  }
]

// ============ Lifecycle ============
onMounted(async () => {
  await loadAgentConfigs()
  await loadSchedules()
})
</script>

<template>
  <div class="schedule-view">
    <!-- Page Header -->
    <PageHeader title="定时任务" :show-border="true" :loading="loading">
      <template #info>
        <div class="summary-strip">
          <span>定时任务可设定 Cron 周期自动执行 Agent 运行，并在询问时静默审批通过</span>
        </div>
      </template>
    </PageHeader>

    <!-- Toolbar -->
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索任务名称/Cron...">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="showCreateModal">
          <Plus :size="14" />
          新增定时任务
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadSchedules" :loading="loading">
          <RefreshCw :size="14" :class="{ spinning: loading }" />
        </a-button>
      </template>
    </PageShoulder>

    <!-- Main Content Table -->
    <div class="page-content">
      <a-table
        :columns="columns"
        :data-source="filteredListData"
        :pagination="pagination"
        :loading="loading"
        row-key="id"
        size="middle"
        :scroll="{ x: 1200 }"
        @change="handleTableChange"
        class="custom-table"
      >
        <!-- Custom Name cell -->
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <div class="name-cell">
              <span class="name-text">{{ record.name }}</span>
              <span
                v-if="record.description"
                class="desc-text text-muted"
                :title="record.description"
              >
                {{ record.description }}
              </span>
            </div>
          </template>

          <!-- Agent Config cell -->
          <template v-else-if="column.key === 'agent_config_id'">
            <div class="agent-config-cell">
              <span class="config-name">
                {{ agentConfigMap[record.agent_config_id] || record.agent_config_id }}
              </span>
            </div>
          </template>

          <!-- Cron cell -->
          <template v-else-if="column.key === 'cron_expr'">
            <div class="cron-cell">
              <code class="cron-code">{{ record.cron_expr }}</code>
              <a-tag class="tz-badge">{{ record.timezone }}</a-tag>
            </div>
          </template>

          <!-- Switch Status -->
          <template v-else-if="column.key === 'enabled'">
            <a-switch
              :checked="record.enabled"
              @change="handleToggleEnabled(record)"
              checked-children="启"
              un-checked-children="停"
            />
          </template>

          <!-- Times -->
          <template v-else-if="column.key === 'last_run_at'">
            <span>{{ formatDateTime(record.last_run_at) }}</span>
          </template>
          <template v-else-if="column.key === 'next_run_at'">
            <span :class="{ 'text-muted': !record.enabled }">
              {{ record.enabled ? formatDateTime(record.next_run_at) : '已暂停' }}
            </span>
          </template>

          <!-- Actions -->
          <template v-else-if="column.key === 'actions'">
            <div class="actions-wrapper">
              <a-tooltip title="立即运行一次">
                <a-button
                  size="small"
                  type="text"
                  class="action-btn play-btn"
                  @click="handleTrigger(record)"
                >
                  <Play :size="14" />
                </a-button>
              </a-tooltip>

              <a-tooltip title="执行日志">
                <a-button
                  size="small"
                  type="text"
                  class="action-btn log-btn"
                  @click="showLogs(record)"
                >
                  <FileText :size="14" />
                </a-button>
              </a-tooltip>

              <a-tooltip title="编辑任务">
                <a-button
                  size="small"
                  type="text"
                  class="action-btn edit-btn"
                  @click="showEditModal(record)"
                >
                  <Edit3 :size="14" />
                </a-button>
              </a-tooltip>

              <a-tooltip title="删除任务">
                <a-button
                  size="small"
                  type="text"
                  class="action-btn delete-btn"
                  @click="handleDelete(record)"
                >
                  <Trash2 :size="14" />
                </a-button>
              </a-tooltip>
            </div>
          </template>
        </template>
      </a-table>
    </div>

    <!-- Create/Edit Drawer -->
    <a-drawer
      v-model:open="isModalVisible"
      :title="isEditing ? '编辑定时任务' : '新增定时任务'"
      :width="600"
      placement="right"
      destroy-on-close
    >
      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical" class="custom-form">
        <a-form-item label="任务名称" name="name">
          <a-input v-model:value="formState.name" placeholder="请输入方便记忆的任务名称" />
        </a-form-item>

        <a-form-item label="任务描述" name="description">
          <a-textarea
            v-model:value="formState.description"
            :rows="2"
            placeholder="可选，定时任务的作用和备注"
          />
        </a-form-item>

        <div class="form-row">
          <a-form-item label="绑定智能体配置" name="agent_config_id" class="form-col-6">
            <a-select
              v-model:value="formState.agent_config_id"
              :options="agentConfigOptions"
              placeholder="选择自动执行的智能体及配置"
              show-search
              option-filter-prop="label"
            />
          </a-form-item>

          <a-form-item label="执行时区" name="timezone" class="form-col-6">
            <a-select v-model:value="formState.timezone" placeholder="请选择时区">
              <a-select-option value="Asia/Shanghai">Asia/Shanghai (北京时间)</a-select-option>
              <a-select-option value="America/New_York"
                >America/New_York (纽约时间)</a-select-option
              >
              <a-select-option value="Europe/London">Europe/London (伦敦时间)</a-select-option>
              <a-select-option value="UTC">UTC (世界协调时)</a-select-option>
            </a-select>
          </a-form-item>
        </div>

        <a-form-item label="定时运行周期 (Cron)" name="cron_expr">
          <CronSelector v-model:value="formState.cron_expr" />
        </a-form-item>

        <div class="form-row" style="margin-top: 16px">
          <a-form-item label="任务状态" name="enabled" class="form-col-6">
            <a-switch
              v-model:checked="formState.enabled"
              checked-children="启用"
              un-checked-children="暂停"
            />
          </a-form-item>
        </div>

        <a-form-item label="Query 提示词 (触发首条输入)" name="query">
          <a-textarea
            v-model:value="formState.query"
            :rows="3"
            placeholder="例如：请播报今天的新闻与重要待办事项"
          />
          <div class="form-item-tip text-muted">
            <Info :size="12" /> 定时触发时，系统将以此 Query
            作为用户第一句话发给智能体自动开启对话。
          </div>
        </a-form-item>

        <a-collapse expand-icon-position="end" :ghost="true" class="advanced-collapse">
          <a-collapse-panel key="advanced" header="高级配置 (JSON 格式参数)">
            <a-form-item label="高级参数 (支持配置 override)" name="config_text">
              <a-textarea
                v-model:value="formState.config_text"
                :rows="4"
                placeholder="{}"
                class="code-textarea"
              />
              <div class="form-item-tip text-muted">
                支持覆盖运行时参数（如 <code>{"temperature": 0.5}</code>，默认可留空
                <code>{}</code>）
              </div>
            </a-form-item>
          </a-collapse-panel>
        </a-collapse>
      </a-form>
      <template #footer>
        <div class="drawer-footer-actions">
          <a-button style="margin-right: 8px" @click="isModalVisible = false">取消</a-button>
          <a-button type="primary" :loading="modalSaving" @click="handleSave">确认保存</a-button>
        </div>
      </template>
    </a-drawer>

    <!-- Logs Drawer -->
    <a-drawer
      v-model:open="isDrawerVisible"
      :title="currentLogSchedule ? `任务执行日志: ${currentLogSchedule.name}` : '执行日志'"
      width="800"
      placement="right"
      destroy-on-close
    >
      <div class="drawer-header-info" v-if="currentLogSchedule">
        <div class="info-item">
          <span class="label text-muted">Cron 周期:</span>
          <code class="cron-code">{{ currentLogSchedule.cron_expr }}</code>
        </div>
        <div class="info-item">
          <span class="label text-muted">触发提示:</span>
          <span class="query-preview" :title="currentLogSchedule.query">{{
            currentLogSchedule.query
          }}</span>
        </div>
      </div>

      <a-table
        :columns="logsColumns"
        :data-source="logsData"
        :pagination="logsPagination"
        :loading="logsLoading"
        row-key="id"
        size="small"
        @change="handleLogsTableChange"
        class="custom-table logs-table"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'created_at'">
            <span>{{ formatDateTime(record.created_at) }}</span>
          </template>

          <!-- Trigger Status -->
          <template v-else-if="column.key === 'status'">
            <a-tag :color="getTriggerStatusColor(record.status)">
              {{ getTriggerStatusText(record.status) }}
            </a-tag>
          </template>

          <!-- Run Status -->
          <template v-else-if="column.key === 'run_status'">
            <a-tag :color="getRunStatusColor(record.run_status)">
              {{ getRunStatusText(record.run_status) }}
            </a-tag>
          </template>

          <!-- Error Details -->
          <template v-else-if="column.key === 'error'">
            <div class="error-cell">
              <span v-if="record.error" class="error-text text-danger" :title="record.error">
                {{ record.error }}
              </span>
              <span v-else class="text-muted">-</span>
            </div>
          </template>

          <!-- Actions -->
          <template v-else-if="column.key === 'actions'">
            <a-button
              v-if="record.thread_id"
              size="small"
              type="link"
              class="lucide-icon-btn text-link"
              @click="navigateToThread(record.thread_id)"
            >
              <span>查看对话</span>
              <ExternalLink :size="12" />
            </a-button>
            <span v-else class="text-muted">-</span>
          </template>
        </template>
      </a-table>
    </a-drawer>
  </div>
</template>

<style scoped lang="less">
.schedule-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: calc(100vh - 64px);
  background-color: var(--gray-25);
}

.summary-strip {
  font-size: 13px;
  color: var(--gray-600);
}

.page-content {
  flex: 1;
  padding: 16px var(--page-padding);
  overflow-y: auto;
}

.custom-table {
  background-color: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: hidden;

  :deep(.ant-table-thead > tr > th) {
    background-color: var(--gray-50);
    color: var(--gray-800);
    font-weight: 600;
    border-bottom: 1px solid var(--gray-100);
  }

  :deep(.ant-table-tbody > tr > td) {
    border-bottom: 1px solid var(--gray-100);
  }

  :deep(.ant-table-row:hover > td) {
    background-color: var(--gray-50) !important;
  }
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;

  .name-text {
    font-weight: 500;
    color: var(--gray-900);
  }

  .desc-text {
    font-size: 12px;
    white-space: nowrap;
    text-overflow: ellipsis;
    overflow: hidden;
    max-width: 200px;
  }
}

.cron-cell {
  display: flex;
  align-items: center;
  gap: 8px;

  .cron-code {
    font-family: monospace;
    background-color: var(--gray-100);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
    color: var(--gray-800);
  }

  .tz-badge {
    margin: 0;
    font-size: 11px;
    background-color: var(--gray-50);
    border-color: var(--gray-200);
    color: var(--gray-600);
  }
}

.actions-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    color: var(--gray-600);
    transition: all 0.2s ease;

    &:hover {
      background-color: var(--gray-100);
    }

    &.play-btn:hover {
      color: var(--main-color);
      background-color: color-mix(in srgb, var(--main-color) 8%, var(--gray-0));
    }

    &.log-btn:hover {
      color: #1890ff;
      background-color: #e6f7ff;
    }

    &.edit-btn:hover {
      color: #722ed1;
      background-color: #f9f0ff;
    }

    &.delete-btn:hover {
      color: #ff4d4f;
      background-color: #fff1f0;
    }
  }
}

.custom-form {
  .form-row {
    display: flex;
    gap: 16px;

    .form-col-6 {
      flex: 0 0 calc(50% - 8px);
    }

    .form-col-8 {
      flex: 0 0 calc(66.6% - 8px);
    }

    .form-col-4 {
      flex: 0 0 calc(33.3% - 8px);
      display: flex;
      align-items: flex-end;
    }
  }

  .switch-form-item {
    margin-bottom: 24px;
    height: 32px;
    display: flex;
    align-items: center;
  }

  .form-item-tip {
    font-size: 12px;
    margin-top: 4px;
    display: flex;
    align-items: center;
    gap: 4px;

    code {
      background-color: var(--gray-100);
      padding: 0 4px;
      border-radius: 2px;
    }
  }

  .code-textarea {
    font-family: monospace;
  }
}

.advanced-collapse {
  margin-top: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background-color: var(--gray-50);

  :deep(.ant-collapse-header) {
    padding: 8px 16px !important;
    font-weight: 500;
    color: var(--gray-700);
  }

  :deep(.ant-collapse-content-box) {
    padding: 16px !important;
    background-color: var(--gray-0);
    border-top: 1px solid var(--gray-150);
  }
}

// Drawer styles
.drawer-header-info {
  background-color: var(--gray-50);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;

  .info-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;

    .label {
      width: 80px;
      font-weight: 500;
    }

    .query-preview {
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
      max-width: 600px;
      color: var(--gray-900);
    }
  }
}

.logs-table {
  .error-cell {
    max-width: 250px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    .error-text {
      font-size: 12px;
    }
  }

  .text-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0;
    font-size: 13px;
  }
}

.text-muted {
  color: var(--gray-500);
}

.text-danger {
  color: #ff4d4f;
}

.spinning {
  animation: spin 1s infinite linear;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>

<style lang="less">
.drawer-footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 4px 16px;
}
</style>
