<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Plus, RefreshCw, Play, Edit3, Trash2, FileText, ExternalLink, Info, Maximize2, Minimize2, LayoutGrid, List, Cpu, Clock, Calendar } from 'lucide-vue-next'

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

// View Mode: 'card' | 'table'
const viewMode = ref(localStorage.getItem('yuxi_schedule_view_mode') || 'card')
watch(viewMode, (newVal) => {
  localStorage.setItem('yuxi_schedule_view_mode', newVal)
})

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

// ============ Resizable & Fullscreen Drawer States & Methods ============
const drawerWidth = ref(600)
const isFullPage = ref(false)
const preFullWidth = ref(600)

const isResizing = ref(false)
let resizePointerId = null
let pendingClientX = 0
let resizeFrameId = 0
let startX = 0
let startWidth = 0
let drawerEl = null // 缓存在高频拖拽中直接操作的真实 DOM 节点

const flushResize = () => {
  resizeFrameId = 0
  if (!isResizing.value || !drawerEl) return

  const deltaX = startX - pendingClientX // 往左拖拽是变宽
  let newWidth = startWidth + deltaX

  // 限制宽度范围：450px 到 屏幕可视宽度的 95%
  const maxWidth = window.innerWidth * 0.95
  if (newWidth < 450) newWidth = 450
  if (newWidth > maxWidth) newWidth = maxWidth

  // 强力覆写所有相关宽度样式，彻底粉碎 ant-design-vue 抽屉内部的任何物理 CSS 宽度约束
  drawerEl.style.setProperty('width', `${newWidth}px`, 'important')
  drawerEl.style.setProperty('min-width', `${newWidth}px`, 'important')
  drawerEl.style.setProperty('max-width', `${newWidth}px`, 'important')
}

const queueResize = (clientX) => {
  pendingClientX = clientX
  if (resizeFrameId) return
  resizeFrameId = window.requestAnimationFrame(flushResize)
}

// 鼠标/指针悬停拉伸宽度方法 - 完美对齐 AgentPanel 拖拽机制
const handleResizeMouseDown = (e) => {
  if (e.button !== 0) return
  e.preventDefault()

  // 1. 采用最稳健的 closest 祖先元素回溯查找，100% 精准锁定当前抽屉的 wrapper 容器，彻底解决 Portal 导致的 Class 移位问题
  drawerEl = e.currentTarget.closest('.ant-drawer-content-wrapper')
  if (!drawerEl) {
    drawerEl = e.currentTarget.closest('.ant-drawer')
  }
  if (!drawerEl) return

  // 2. 强行在真实 DOM 上瞬间切断 Transition 过渡动画，保障拖拽瞬间毫无阻尼
  drawerEl.style.setProperty('transition', 'none', 'important')

  isResizing.value = true
  resizePointerId = e.pointerId
  startX = e.clientX
  startWidth = drawerWidth.value
  pendingClientX = e.clientX

  // 锁定指针与避免文字选中
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'

  // 将指针捕获锁定到该手柄，确保即使指针移出元素外，也能完美捕获所有指针移动事件
  e.currentTarget?.setPointerCapture?.(e.pointerId)

  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', stopResize)
  window.addEventListener('pointercancel', stopResize)
}

const onPointerMove = (e) => {
  if (!isResizing.value || e.pointerId !== resizePointerId) return
  queueResize(e.clientX)
}

const stopResize = (e) => {
  if (!isResizing.value || (e && e.pointerId !== resizePointerId)) return

  if (resizeFrameId) {
    window.cancelAnimationFrame(resizeFrameId)
    resizeFrameId = 0
  }

  // 释放指针捕获
  try {
    const handleEl = document.querySelector('.resizable-drawer .drawer-resize-handle')
    if (handleEl && resizePointerId !== null) {
      handleEl.releasePointerCapture(resizePointerId)
    }
  } catch (err) {
    console.warn('释放指针捕获失败:', err)
  }

  // 2. 拖拽结束，恢复真实 DOM 上的过渡动画并干净擦除所有强制宽度样式覆盖
  if (drawerEl) {
    drawerEl.style.removeProperty('transition')
    drawerEl.style.removeProperty('width')
    drawerEl.style.removeProperty('min-width')
    drawerEl.style.removeProperty('max-width')
  }

  let finalClientX = e ? e.clientX : pendingClientX
  const deltaX = startX - finalClientX
  let finalWidth = startWidth + deltaX
  const maxWidth = window.innerWidth * 0.95
  if (finalWidth < 450) finalWidth = 450
  if (finalWidth > maxWidth) finalWidth = maxWidth

  // 3. 拖拽结束时，一次性写入并同步最终宽度到 Vue 响应式状态中
  drawerWidth.value = finalWidth
  preFullWidth.value = finalWidth

  isResizing.value = false
  resizePointerId = null
  drawerEl = null // 释放 DOM 引用，防止潜在内存泄露
  document.body.style.cursor = ''
  document.body.style.userSelect = ''

  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
}

// 页面卸载时进行彻底的垃圾回收与事件解绑
onUnmounted(() => {
  if (resizeFrameId) {
    window.cancelAnimationFrame(resizeFrameId)
  }
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
})

// 切换“全屏”最大化状态（不盖住左侧导航边栏，保留平台级导航和全局控制感）
const toggleFullscreen = () => {
  if (isFullPage.value) {
    drawerWidth.value = preFullWidth.value
    isFullPage.value = false
  } else {
    preFullWidth.value = drawerWidth.value

    // 动态获取左侧边导航栏的实际物理宽度，做动态适配
    const sidebarEl = document.querySelector('.app-layout > .header')
    const sidebarWidth = sidebarEl ? sidebarEl.offsetWidth : 252

    drawerWidth.value = window.innerWidth - sidebarWidth
    isFullPage.value = true
  }
}

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
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['5', '10', '20', '50']
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

// 基于 LocalPagination 的高精度本地 pageSize 数据裁剪计算属性
const paginatedListData = computed(() => {
  const startIndex = (pagination.current - 1) * pagination.pageSize
  const endIndex = startIndex + pagination.pageSize
  return filteredListData.value.slice(startIndex, endIndex)
})

const paginatedLogsData = computed(() => {
  const startIndex = (logsPagination.current - 1) * logsPagination.pageSize
  const endIndex = startIndex + logsPagination.pageSize
  return logsData.value.slice(startIndex, endIndex)
})

// 搜索条件或数据源改变时，重置当前页码，防止本地溢出
watch(searchQuery, () => {
  pagination.current = 1
})
watch(filteredListData, () => {
  pagination.current = 1
})
watch(logsData, () => {
  logsPagination.current = 1
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
      limit: 200, // 拉取全量，让前端在本地进行高精度的 LocalPagination 裁剪，极致翻页响应
      offset: 0
    })

    if (res && res.success) {
      listData.value = res.data || []
      pagination.total = res.data ? res.data.length : 0
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
  // 本地分页无需频繁重新调用接口拉取，直接刷新前端状态，零延迟响应
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
      limit: 200, // 拉取全量日志历史，并在本地进行高精度小规格 pageSize 裁剪
      offset: 0
    })
    if (res && res.success) {
      logsData.value = res.data || []
      logsPagination.total = res.data ? res.data.length : 0
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
  if (pag.pageSize) {
    logsPagination.pageSize = pag.pageSize
  }
  // 本地翻页无需重新向后端请求，零延迟瞬间响应
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
    width: 220,
    fixed: 'left' // 将任务名称锁定在最左侧，确保在横向滚动或大分辨率下关键标识雷打不动
  },
  {
    title: '绑定的智能体配置',
    dataIndex: 'agent_config_id',
    key: 'agent_config_id',
    // 去掉固定宽度以承接自适应弹性拉伸，完美吸收多余空间
  },
  {
    title: '定时周期 (Cron)',
    dataIndex: 'cron_expr',
    key: 'cron_expr',
    width: 180
  },
  {
    title: '状态',
    dataIndex: 'enabled',
    key: 'enabled',
    width: 90
  },
  {
    title: '最近执行时间',
    dataIndex: 'last_run_at',
    key: 'last_run_at',
    width: 160
  },
  {
    title: '下次预计执行',
    dataIndex: 'next_run_at',
    key: 'next_run_at',
    width: 160
  },
  {
    title: '操作',
    key: 'actions',
    fixed: 'right',
    width: 150
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
        <!-- 双态视图切换分段控制器 -->
        <div class="view-toggle-group">
          <button
            class="view-toggle-btn"
            :class="{ active: viewMode === 'card' }"
            @click="viewMode = 'card'"
            title="网格卡片视图"
            type="button"
          >
            <LayoutGrid :size="13" />
          </button>
          <button
            class="view-toggle-btn"
            :class="{ active: viewMode === 'table' }"
            @click="viewMode = 'table'"
            title="表格列表视图"
            type="button"
          >
            <List :size="13" />
          </button>
        </div>

        <a-button type="primary" class="lucide-icon-btn" @click="showCreateModal">
          <Plus :size="14" />
          新增定时任务
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadSchedules" :loading="loading">
          <RefreshCw :size="14" :class="{ spinning: loading }" />
        </a-button>
      </template>
    </PageShoulder>

    <!-- Main Content Grid / Table -->
    <div class="page-content">
      <!-- 1. 卡片网格视图 (Card Grid View) -->
      <div v-if="viewMode === 'card'" class="card-view-container">
        <div v-if="paginatedListData.length > 0" class="schedule-card-grid">
          <div
            v-for="record in paginatedListData"
            :key="record.id"
            class="schedule-card"
            :class="{ 'is-disabled': !record.enabled }"
          >
            <!-- 卡片头部：任务标题与状态 Toggle -->
            <div class="card-header">
              <div class="card-title-area">
                <h3 class="card-title" :title="record.name" @click="showEditModal(record)">
                  {{ record.name }}
                </h3>
                <span v-if="record.description" class="card-description text-muted" :title="record.description">
                  {{ record.description }}
                </span>
              </div>
              <div class="card-status-switch">
                <a-switch
                  :checked="record.enabled"
                  @change="handleToggleEnabled(record)"
                  checked-children="启"
                  un-checked-children="停"
                  size="small"
                />
              </div>
            </div>

            <!-- 卡片主体：核心配置参数 -->
            <div class="card-body">
              <!-- 绑定智能体配置 -->
              <div class="body-info-row">
                <span class="info-label text-muted">
                  <Cpu :size="13" /> 绑定的智能体配置
                </span>
                <span class="info-value agent-name-badge">
                  {{ agentConfigMap[record.agent_config_id] || record.agent_config_id }}
                </span>
              </div>

              <!-- Cron 表达式及周期 -->
              <div class="body-info-row">
                <span class="info-label text-muted">
                  <Clock :size="13" /> 定时周期 (Cron)
                </span>
                <span class="info-value cron-badge-container">
                  <code class="cron-code-text">{{ record.cron_expr }}</code>
                  <span class="tz-badge-text">{{ record.timezone }}</span>
                </span>
              </div>
            </div>

            <!-- 卡片尾部：最近及预计下一次执行时间 -->
            <div class="card-footer">
              <div class="time-stat-row">
                <div class="time-stat-item">
                  <span class="time-label text-muted">最近执行</span>
                  <span class="time-value">
                    <Calendar :size="12" /> {{ formatDateTime(record.last_run_at) }}
                  </span>
                </div>
                <div class="time-stat-item">
                  <span class="time-label text-muted">下次预计</span>
                  <span class="time-value" :class="{ 'paused-text': !record.enabled }">
                    <Calendar :size="12" />
                    {{ record.enabled ? formatDateTime(record.next_run_at) : '已暂停' }}
                  </span>
                </div>
              </div>
            </div>

            <!-- 卡片悬浮/常规动作控制面板 -->
            <div class="card-actions-panel">
              <a-tooltip title="立即运行一次" :mouse-enter-delay="0.4">
                <button class="card-action-btn play-btn" @click="handleTrigger(record)" type="button">
                  <Play :size="13" />
                </button>
              </a-tooltip>
              
              <a-tooltip title="执行日志" :mouse-enter-delay="0.4">
                <button class="card-action-btn log-btn" @click="showLogs(record)" type="button">
                  <FileText :size="13" />
                </button>
              </a-tooltip>
              
              <a-tooltip title="编辑任务" :mouse-enter-delay="0.4">
                <button class="card-action-btn edit-btn" @click="showEditModal(record)" type="button">
                  <Edit3 :size="13" />
                </button>
              </a-tooltip>
              
              <a-tooltip title="删除任务" :mouse-enter-delay="0.4">
                <button class="card-action-btn delete-btn" @click="handleDelete(record)" type="button">
                  <Trash2 :size="13" />
                </button>
              </a-tooltip>
            </div>
          </div>
        </div>

        <!-- 极简精美的空数据展示 -->
        <div v-else class="empty-glass-container animate-fade-in">
          <a-empty description="暂无符合条件的定时任务">
            <template #image>
              <div class="empty-icon-glow">
                <Cpu :size="40" stroke="var(--main-400)" />
              </div>
            </template>
            <a-button type="primary" @click="showCreateModal" style="margin-top: 16px;">
              <Plus :size="14" /> 新增定时任务
            </a-button>
          </a-empty>
        </div>

        <!-- 卡片下的分页控制栏 -->
        <div v-if="filteredListData.length > 0" class="card-pagination-wrapper">
          <a-pagination
            v-model:current="pagination.current"
            v-model:pageSize="pagination.pageSize"
            :total="pagination.total"
            :show-size-changer="pagination.showSizeChanger"
            :page-size-options="pagination.pageSizeOptions"
            @change="(page, size) => handleTableChange({ current: page, pageSize: size })"
          />
        </div>
      </div>

      <!-- 2. 表格列表视图 (Table View - 原有逻辑，高雅融合) -->
      <div v-else class="table-view-container">
        <a-table
          :columns="columns"
          :data-source="paginatedListData"
          :pagination="false"
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

        <!-- 表格外部沉底分页控制器 -->
        <div v-if="filteredListData.length > 0" class="table-pagination-wrapper">
          <a-pagination
            v-model:current="pagination.current"
            v-model:pageSize="pagination.pageSize"
            :total="pagination.total"
            :show-size-changer="pagination.showSizeChanger"
            :page-size-options="pagination.pageSizeOptions"
            @change="(page, size) => handleTableChange({ current: page, pageSize: size })"
          />
        </div>
      </div>
    </div>

    <!-- Create/Edit Drawer -->
    <a-drawer
      v-model:open="isModalVisible"
      :title="isEditing ? '编辑定时任务' : '新增定时任务'"
      :width="drawerWidth"
      placement="right"
      destroy-on-close
      class="resizable-drawer"
      :class="{ 'is-resizing': isResizing }"
    >
      <!-- 拖拽拉伸手柄条 -->
      <div class="drawer-resize-handle" @pointerdown="handleResizeMouseDown"></div>

      <!-- 头部自定义全屏按钮 -->
      <template #extra>
        <a-button type="text" class="fullscreen-toggle-btn" @click="toggleFullscreen">
          <component :is="isFullPage ? Minimize2 : Maximize2" :size="14" />
        </a-button>
      </template>

      <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical" class="custom-form">
        <!-- 1. 核心指令：Query 提示词 -->
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

        <!-- 2. 基础信息：名称与描述 -->
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

        <!-- 3. 运行环境：绑定智能体 与 状态开关 -->
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

          <a-form-item label="任务状态" name="enabled" class="form-col-6">
            <a-switch
              v-model:checked="formState.enabled"
              checked-children="启用"
              un-checked-children="暂停"
              style="margin-top: 6px"
            />
          </a-form-item>
        </div>

        <!-- 4. 调度周期：执行时区 与 Cron 表达式 -->
        <div class="form-row" style="margin-top: 8px">
          <a-form-item label="执行时区" name="timezone" class="form-col-12" style="width: 100%">
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

        <!-- 5. 高级极客配置 -->
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
      class="logs-drawer"
    >
      <div class="logs-drawer-content" v-if="currentLogSchedule">
        <div class="drawer-header-info">
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
          :data-source="paginatedLogsData"
          :pagination="false"
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

        <!-- 日志表格外部独立分页组件，沉在最底部 -->
        <div v-if="logsData.length > 0" class="logs-pagination-wrapper">
          <a-pagination
            v-model:current="logsPagination.current"
            v-model:pageSize="logsPagination.pageSize"
            :total="logsPagination.total"
            :show-size-changer="logsPagination.showSizeChanger"
            :page-size-options="logsPagination.pageSizeOptions"
            @change="(page, size) => handleLogsTableChange({ current: page, pageSize: size })"
          />
        </div>
      </div>
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
  padding: 20px var(--page-padding);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* 分段视图控制器设计 */
.view-toggle-group {
  display: inline-flex;
  background-color: var(--gray-100);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 3px;
  gap: 3px;
  margin-right: 8px;
  align-items: center;

  .view-toggle-btn {
    border: none;
    background: transparent;
    height: 26px;
    width: 28px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    color: var(--gray-500);
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    outline: none;

    &:hover {
      color: var(--gray-800);
      background-color: var(--gray-200);
    }

    &.active {
      color: var(--main-color);
      background-color: var(--gray-0);
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
    }
  }
}

/* 卡片视图容器与响应式网格 */
.card-view-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 20px;
  justify-content: space-between;
  height: 100%;
}

.schedule-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
  width: 100%;
}

/* 极致精致的卡片 */
.schedule-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background-color: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  padding: 18px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.015);

  &:hover {
    transform: translateY(-4px);
    border-color: var(--main-300);
    box-shadow: 0 10px 20px -8px color-mix(in srgb, var(--main-color) 12%, transparent),
                0 4px 12px rgba(0, 0, 0, 0.02);

    .card-actions-panel {
      opacity: 1;
      transform: translateY(0);
    }
  }

  /* 暂停/停用状态卡片：精致淡雅的明暗度降低 */
  &.is-disabled {
    border-color: var(--gray-100);
    background-color: color-mix(in srgb, var(--gray-25) 50%, var(--gray-0));

    .card-title {
      color: var(--gray-500);
    }

    .agent-name-badge {
      background-color: var(--gray-100);
      color: var(--gray-500);
      border-color: var(--gray-150);
    }
    
    .cron-code-text {
      background-color: var(--gray-100);
      color: var(--gray-500);
    }
  }
}

/* 卡片头部 */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;

  .card-title-area {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }

  .card-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--gray-900);
    cursor: pointer;
    white-space: nowrap;
    text-overflow: ellipsis;
    overflow: hidden;
    transition: color 0.2s ease;

    &:hover {
      color: var(--main-bright);
    }
  }

  .card-description {
    font-size: 12px;
    color: var(--gray-500);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.5;
    min-height: 18px;
  }

  .card-status-switch {
    flex-shrink: 0;
    padding-top: 2px;
  }
}

/* 卡片主体核心芯片区 */
.card-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-top: 1px dashed var(--gray-150);
  border-bottom: 1px dashed var(--gray-150);
  padding: 12px 0;
  margin-bottom: 12px;

  .body-info-row {
    display: flex;
    flex-direction: column;
    gap: 6px;

    .info-label {
      font-size: 11px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-weight: 500;
      color: var(--gray-500);
    }

    .info-value {
      font-size: 13px;
      font-weight: 500;
      color: var(--gray-800);
      min-width: 0;
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
    }
  }

  /* 智能体标签 */
  .agent-name-badge {
    background-color: color-mix(in srgb, var(--main-color) 6%, var(--gray-25));
    border: 1px solid color-mix(in srgb, var(--main-color) 12%, var(--gray-150));
    color: var(--main-700);
    padding: 3px 8px;
    border-radius: 6px;
    display: inline-block;
    max-width: 100%;
    font-size: 12px;
  }

  /* Cron芯片 */
  .cron-badge-container {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;

    .cron-code-text {
      font-family: Menlo, Monaco, Consolas, "Courier New", monospace;
      background-color: var(--gray-50);
      border: 1px solid var(--gray-200);
      color: var(--gray-800);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }

    .tz-badge-text {
      font-size: 11px;
      color: var(--gray-500);
    }
  }
}

/* 卡片尾部时间展示 */
.card-footer {
  margin-top: auto;
  padding-bottom: 8px;

  .time-stat-row {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .time-stat-item {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1;
    min-width: 0;

    .time-label {
      font-size: 10px;
      color: var(--gray-500);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .time-value {
      font-size: 11px;
      color: var(--gray-700);
      font-family: monospace;
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      
      .paused-text {
        color: var(--gray-400);
        font-style: italic;
      }
    }
  }
}

/* 常驻/悬浮动作浮动底板 */
.card-actions-panel {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 40px;
  background-color: var(--gray-50);
  border-top: 1px solid var(--gray-150);
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 0 10px;
  opacity: 0;
  transform: translateY(100%);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);

  .card-action-btn {
    border: none;
    background: transparent;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 6px;
    color: var(--gray-500);
    cursor: pointer;
    transition: all 0.2s ease;
    outline: none;

    &:hover {
      background-color: var(--gray-150);
      color: var(--gray-800);
    }

    &.play-btn:hover {
      color: var(--main-bright);
      background-color: color-mix(in srgb, var(--main-bright) 8%, var(--gray-0));
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

/* 分页器包装 - 与表格完全像素级一致，彻底锁死底部以消灭切换抖动 */
.card-pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0 0;
  border-top: 1px dashed var(--gray-150);
  margin-top: auto;
}

/* 精致毛玻璃 Empty 态 */
.empty-glass-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  background-color: var(--gray-0);
  border: 1px dashed var(--gray-200);
  border-radius: 12px;
  width: 100%;
  padding: 40px;

  .empty-icon-glow {
    width: 60px;
    height: 60px;
    margin: 0 auto 12px;
    border-radius: 50%;
    background-color: color-mix(in srgb, var(--main-color) 6%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid color-mix(in srgb, var(--main-color) 10%, transparent);
    box-shadow: 0 0 20px -5px rgba(var(--main-color), 0.1);
  }
}

/* 表格视图容器 */
.table-view-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  height: 100%;
}

.custom-table {
  background-color: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.015);
  margin-bottom: 16px;
  flex: 1;
  display: flex;
  flex-direction: column;

  :deep(.ant-table) {
    background: transparent;
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  :deep(.ant-spin-nested-loading) {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  :deep(.ant-spin-container) {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  :deep(.ant-table-container) {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  :deep(.ant-table-content) {
    flex: 1;
    overflow: auto !important;
    display: flex;
    flex-direction: column;
  }

  :deep(table) {
    width: 100%;
  }

  :deep(.ant-table-thead > tr > th) {
    background-color: var(--gray-50);
    color: var(--gray-800);
    font-weight: 600;
    border-bottom: 1px solid var(--gray-150);
    font-size: 13px;
  }

  :deep(.ant-table-tbody > tr > td) {
    border-bottom: 1px solid var(--gray-100);
    transition: background 0.2s ease;
  }

  :deep(.ant-table-row:hover > td) {
    background-color: var(--gray-25) !important;
  }
}

/* 表格分页器独立包装，完美沉底 */
.table-pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0 0;
  border-top: 1px dashed var(--gray-150);
  margin-top: auto;
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

/* 日志抽屉撑满全屏及自适应布局 */
.logs-drawer {
  :deep(.ant-drawer-body) {
    padding: 20px;
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden; /* 防止抽屉外部滚动，改由内部表格自滚动，更加高级考究 */
  }
}

.logs-drawer-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.logs-table {
  flex: 1;
  margin-bottom: 12px;

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

/* 日志分页器极致沉底 */
.logs-pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  padding: 10px 0 0;
  border-top: 1px dashed var(--gray-150);
  margin-top: auto;
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

.resizable-drawer {
  position: relative;

  .ant-drawer-content-wrapper {
    position: relative;
  }

  /* 拖拽拉伸进行中时，强制关闭 transition 过渡动画，防止由于动画阻尼产生严重的卡顿或拉扯感，完美保障拖拽绝对贴手丝滑 */
  &.is-resizing {
    .ant-drawer-content-wrapper {
      transition: none !important;
    }
  }

  .drawer-resize-handle {
    position: absolute;
    left: -6px; /* 居中于左边缘线 */
    top: 0;
    bottom: 0;
    width: 12px; /* 舒适的热区宽度，让鼠标非常容易捕捉 */
    cursor: col-resize;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: transparent;
    touch-action: none;

    /* 仿 AgentPanel 文件系统同款灰色半透明小长条指示器 */
    &::after {
      content: '';
      width: 4px;
      height: 32px;
      background: var(--gray-300);
      border-radius: 2px;
      transition: background 0.2s, height 0.2s, width 0.2s;
    }

    /* 悬浮及拖动时指示条高亮为主题色 */
    &:hover::after, &:active::after {
      background: var(--main-400) !important;
      height: 38px; /* 悬浮时微增高度，充满呼吸感 */
    }
  }
}

/* 头部全屏切换控制按钮美化 */
.fullscreen-toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  color: var(--gray-500);
  transition: all 0.2s ease;
  padding: 0;
  border: none;

  &:hover {
    color: var(--main-color) !important;
    background-color: rgba(0, 0, 0, 0.04) !important;
  }
}
</style>
