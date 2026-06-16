<template>
  <div class="tab-content connections-tab">
    <div class="connection-command-bar">
      <div class="connection-command-copy">
        <h3>MCP 连接管理</h3>
        <p>为不同范围绑定长期凭据，运行时会按当前用户、部门或全局范围自动选择。</p>
      </div>
      <a-button
        type="primary"
        :disabled="!hasAuthConfig"
        class="lucide-icon-btn"
        @click="openCreateForm"
      >
        <Plus :size="14" />
        <span>新建连接</span>
      </a-button>
    </div>

    <a-alert
      v-if="!hasAuthConfig"
      type="info"
      show-icon
      message="当前 MCP 未启用动态鉴权"
      description="未配置 auth_config 时，运行时会直接使用 MCP 的基础配置，不需要额外维护连接。"
    />

    <div v-else class="connection-summary-strip">
      <div class="connection-summary-item">
        <span class="summary-label">认证模式</span>
        <strong>{{ providerLabelMap[server.auth_config?.provider] || server.auth_config?.provider || '未配置' }}</strong>
      </div>
      <div class="connection-summary-item">
        <span class="summary-label">默认绑定</span>
        <strong>{{ authBindingScopeLabel }}</strong>
      </div>
      <div class="connection-summary-item">
        <span class="summary-label">可用连接</span>
        <strong>{{ activeConnectionCount }}</strong>
      </div>
      <div class="connection-summary-item">
        <span class="summary-label">需处理</span>
        <strong>{{ attentionConnectionCount }}</strong>
      </div>
    </div>

    <div class="connection-list-toolbar">
      <div class="connection-filter-group">
        <a-segmented v-model:value="connectionFilter" :options="connectionFilterOptions" />
        <a-input
          v-model:value="connectionSearchText"
          allow-clear
          class="connection-search-input"
          placeholder="搜索连接名、绑定对象"
        >
          <template #prefix>
            <Search :size="14" />
          </template>
        </a-input>
      </div>
      <div class="connection-page-controls">
        <span class="connection-result-count">共 {{ connectionTotal }} 条</span>
        <a-pagination
          v-if="connectionTotal > connectionPageSize"
          v-model:current="connectionPage"
          v-model:page-size="connectionPageSize"
          size="small"
          :total="connectionTotal"
          :page-size-options="['12', '24', '48']"
          show-size-changer
          show-less-items
        />
      </div>
    </div>

    <a-spin :spinning="connectionsLoading">
      <div v-if="connectionsError" class="detail-empty">
        <a-empty :description="connectionsError" />
      </div>
      <div v-else-if="connections.length === 0" class="connection-empty-state">
        <a-empty :description="connectionEmptyDescription" />
        <a-button
          v-if="hasAuthConfig && !hasConnectionListFilter"
          type="primary"
          class="lucide-icon-btn"
          @click="openCreateForm"
        >
          <Plus :size="14" />
          <span>新建连接</span>
        </a-button>
        <a-button v-else-if="hasConnectionListFilter" @click="resetConnectionFilters">
          清除筛选
        </a-button>
      </div>
      <div v-else class="connection-list-body">
        <div class="connection-cards-grid">
          <McpConnectionCard
            v-for="connection in connections"
            :key="connection.id"
            :connection="connection"
            :title="getConnectionTitle(connection)"
            :subtitle="connection.external_subject || ''"
            :scope-label="getConnectionScopeLabel(connection.scope_type)"
            :scope-target-label="getConnectionScopeTargetLabel(connection)"
            :scope-mismatch="!isConnectionScopeMatched(connection)"
            :issue="getConnectionIssue(connection)"
            :last-info="getConnectionLastInfo(connection)"
            :status-switch-label="getConnectionStatusSwitchLabel(connection)"
            :status-toggle-tooltip="getConnectionStatusToggleTooltip(connection)"
            :test-tooltip="getConnectionTestTooltip(connection)"
            :reauthorize-tooltip="getConnectionReauthorizeTooltip(connection)"
            :can-toggle-status="canToggleConnectionStatus(connection)"
            :can-test="canTestConnection(connection)"
            :can-reauthorize="canReauthorizeConnection(connection)"
            :status-loading="isActionLoading(connection, 'status')"
            :test-loading="isActionLoading(connection, 'test')"
            :reauthorize-loading="isActionLoading(connection, 'reauth')"
            :issue-loading="isIssueActionLoading(connection)"
            @edit="startEditForm"
            @test="handleTestConnection"
            @reauthorize="handleReauthorizeConnection"
            @delete="deleteConnection"
            @toggle-status="handleToggleConnectionStatus"
            @issue-action="handleConnectionIssueAction"
          />
        </div>
        <div v-if="connectionTotal > connectionPageSize" class="connection-pagination">
          <a-pagination
            v-model:current="connectionPage"
            v-model:page-size="connectionPageSize"
            :total="connectionTotal"
            :page-size-options="['12', '24', '48']"
            show-size-changer
            show-less-items
          />
        </div>
      </div>
    </a-spin>

    <a-drawer
      v-model:open="isOpen"
      :title="connectionDrawerTitle"
      placement="right"
      width="min(560px, calc(100vw - 24px))"
      :body-style="{ padding: 0 }"
      destroy-on-close
      class="mcp-connection-drawer"
      @close="closeForm"
    >
      <McpConnectionForm
        v-model="form"
        variant="drawer"
        show-scope
        :is-editing="isEditing"
        :submitting="submitting"
        :show-scope-id-field="showScopeIdField"
        :available-scope-options="availableConnectionScopeOptions"
        :department-list="departmentList"
        :user-list="userList"
        :scope-options-loading="isFetchingScopeOptions"
        :scope-id-label="scopeIdLabel"
        :scope-id-placeholder="scopeIdPlaceholder"
        :secret-fields="credentialSecretFields"
        :credential-hint="credentialHint"
        :submit-text="isEditing ? '保存连接' : '创建连接'"
        @submit="handleSubmitConnection"
        @cancel="closeForm"
      />
    </a-drawer>
  </div>
</template>

<script setup>
import { computed, reactive, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Building2, Globe2, Plus, Search, UserRound } from 'lucide-vue-next'
import { departmentApi } from '@/apis/department_api'
import { mcpApi } from '@/apis/mcp_api'
import { userApi } from '@/apis/user_api'
import { useMcpConnectionActions } from '@/composables/useMcpConnectionActions'
import { useMcpConnectionCardState } from '@/composables/useMcpConnectionCardState'
import { useMcpConnectionForm } from '@/composables/useMcpConnectionForm'
import { extractSecretFieldNames } from '@/utils/mcpAuthConfigBuilder'
import {
  MCP_CONNECTION_SCOPE_LABELS,
  isMcpConnectionCredentialMissing
} from '@/utils/mcpConnectionUtils'
import McpConnectionCard from './McpConnectionCard.vue'
import McpConnectionForm from './McpConnectionForm.vue'

const props = defineProps({
  server: { type: Object, default: null },
  active: { type: Boolean, default: false }
})

const connectionTotalCount = defineModel('count', { type: Number, default: 0 })

const userList = shallowRef([])
const departmentList = shallowRef([])
const isFetchingScopeOptions = shallowRef(false)
const scopeOptionsLoaded = shallowRef(false)

const connections = shallowRef([])
const connectionsLoading = shallowRef(false)
const connectionsError = shallowRef(null)
const connectionFilter = shallowRef('all')
const connectionSearchText = shallowRef('')
const connectionPage = shallowRef(1)
const connectionPageSize = shallowRef(12)
const connectionTotal = shallowRef(0)
const connectionSummary = reactive({
  total: 0,
  active: 0,
  attention: 0,
  disabled: 0
})

const connectionScopeOptions = [
  {
    value: 'system',
    label: '全局共享',
    description: '所有用户共用',
    icon: Globe2
  },
  {
    value: 'department',
    label: '部门共享',
    description: '按部门隔离',
    icon: Building2
  },
  {
    value: 'user',
    label: '个人专用',
    description: '按用户隔离',
    icon: UserRound
  }
]

const scopeLabelMap = MCP_CONNECTION_SCOPE_LABELS
const providerLabelMap = {
  none: '不启用',
  bound_secret: '绑定长期密钥',
  custom_http_token: '接口换 Token',
  stdio_env: 'StdIO 环境变量',
  client_credentials: 'OAuth2 客户端凭证'
}
const connectionFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '生效中', value: 'active' },
  { label: '需处理', value: 'attention' },
  { label: '未启用', value: 'disabled' }
]
const validConnectionScopeTypes = ['system', 'department', 'user']

const hasAuthConfig = computed(
  () => !!props.server?.auth_config && Object.keys(props.server.auth_config).length > 0
)
const effectiveConnectionScopeType = computed(() => {
  const bindingScope = props.server?.auth_config?.binding_scope
  return validConnectionScopeTypes.includes(bindingScope) ? bindingScope : ''
})
const availableConnectionScopeOptions = computed(() => {
  if (!effectiveConnectionScopeType.value) return connectionScopeOptions
  return connectionScopeOptions.filter(
    (option) => option.value === effectiveConnectionScopeType.value
  )
})
const authBindingScopeLabel = computed(() => {
  const bindingScope = props.server?.auth_config?.binding_scope
  return scopeLabelMap[bindingScope] || '未限定'
})
const activeConnectionCount = computed(() => connectionSummary.active || 0)
const attentionConnectionCount = computed(() => connectionSummary.attention || 0)
const hasConnectionListFilter = computed(
  () => connectionFilter.value !== 'all' || Boolean(connectionSearchText.value.trim())
)
const connectionEmptyDescription = computed(() => {
  if (hasConnectionListFilter.value) return '没有匹配的连接。'
  if (hasAuthConfig.value) return '暂无连接。创建连接后，运行时会按绑定范围自动选择凭据。'
  return '当前 MCP 没有启用动态鉴权连接。'
})
const credentialSecretFields = computed(() => extractSecretFieldNames(props.server?.auth_config || {}))
const connectionCredentialsRequired = computed(() => credentialSecretFields.value.length > 0)

const getDefaultConnectionScopeType = () =>
  availableConnectionScopeOptions.value[0]?.value || 'department'

const {
  isOpen,
  submitting,
  editingConnectionId,
  form,
  isEditing,
  openCreateForm,
  startEditForm,
  closeForm,
  validateCredential,
  buildCredential,
  buildBasePayload
} = useMcpConnectionForm({
  secretFields: credentialSecretFields,
  getDefaultScopeType: getDefaultConnectionScopeType,
  onError: message.error
})

const connectionDrawerTitle = computed(() => (isEditing.value ? '编辑连接' : '新建连接'))
const showScopeIdField = computed(() => form.scopeType !== 'system')
const scopeIdLabel = computed(() => {
  if (form.scopeType === 'department') return '部门'
  if (form.scopeType === 'user') return '用户'
  return '范围标识'
})
const scopeIdPlaceholder = computed(() => {
  if (form.scopeType === 'department') return '请选择部门'
  if (form.scopeType === 'user') return '请选择用户'
  return '留空默认 global'
})
const credentialHint = computed(() => {
  if (isEditing.value) {
    return '为安全起见不回显已有凭据；留空表示保持原值。'
  }
  if (credentialSecretFields.value.length > 0) {
    return '系统已根据认证配置推导出需要录入的密钥字段。'
  }
  return '当前认证配置没有声明密钥字段，可直接粘贴长期 token。'
})

const getConnectionScopeLabel = (scopeType) => scopeLabelMap[scopeType] || scopeType || '未知范围'
const isConnectionScopeMatched = (connection) =>
  !effectiveConnectionScopeType.value ||
  connection?.scope_type === effectiveConnectionScopeType.value
const isConnectionCredentialMissing = (connection) =>
  isMcpConnectionCredentialMissing(connection, connectionCredentialsRequired.value)

const getConnectionScopeTargetLabel = (connection) => {
  const scopeId = String(connection?.scope_id || '')
  if (connection?.scope_type === 'system') {
    return '全部用户'
  }
  if (connection?.scope_type === 'department') {
    const department = departmentList.value.find((item) => String(item.id) === scopeId)
    return department ? `${department.name} (#${department.id})` : `部门 #${scopeId || '-'}`
  }
  if (connection?.scope_type === 'user') {
    const user = userList.value.find(
      (item) => String(item.id) === scopeId || String(item.user_id) === scopeId
    )
    if (!user) return `用户 #${scopeId || '-'}`
    return user.username === user.user_id ? user.username : `${user.username} (${user.user_id})`
  }
  return scopeId || '未指定'
}

const getConnectionTitle = (connection) =>
  connection.display_name ||
  `${getConnectionScopeLabel(connection.scope_type)} ${getConnectionScopeTargetLabel(connection)}`

const {
  canToggleConnectionStatus,
  canTestConnection,
  canReauthorizeConnection,
  getConnectionStatusSwitchLabel,
  getConnectionStatusToggleTooltip,
  getConnectionTestTooltip,
  getConnectionReauthorizeTooltip,
  getConnectionIssue,
  getConnectionLastInfo
} = useMcpConnectionCardState({
  isScopeMatched: isConnectionScopeMatched,
  isCredentialMissing: isConnectionCredentialMissing,
  authBindingScopeLabel
})

const fetchConnections = async () => {
  if (!props.server) return
  try {
    connectionsLoading.value = true
    connectionsError.value = null
    const result = await mcpApi.getMcpServerConnections(props.server.name, {
      paginated: true,
      status: connectionFilter.value,
      search: connectionSearchText.value.trim(),
      page: connectionPage.value,
      page_size: connectionPageSize.value
    })
    if (result.success) {
      applyConnectionsResult(result.data)
    } else {
      connectionsError.value = result.message || '获取连接列表失败'
      resetConnectionList()
    }
  } catch (err) {
    connectionsError.value = err.message || '获取连接列表失败'
    resetConnectionList()
  } finally {
    connectionsLoading.value = false
  }
}

function applyConnectionsResult(data) {
  if (Array.isArray(data)) {
    connections.value = data
    connectionTotal.value = data.length
    Object.assign(connectionSummary, {
      total: data.length,
      active: data.filter(
        (connection) =>
          connection.status === 'active' &&
          isConnectionScopeMatched(connection) &&
          !isConnectionCredentialMissing(connection)
      ).length,
      attention: data.filter((connection) => Boolean(getConnectionIssue(connection))).length,
      disabled: data.filter((connection) => connection.status === 'disabled').length
    })
    connectionTotalCount.value = connectionSummary.total || connectionTotal.value
    return
  }

  const pageData = data || {}
  const nextConnections = pageData.items || []
  const nextTotal = pageData.total || 0
  const nextPageSize = pageData.page_size || connectionPageSize.value
  if (connectionPage.value > 1 && nextConnections.length === 0 && nextTotal > 0) {
    connectionPage.value = Math.ceil(nextTotal / nextPageSize)
    fetchConnections()
    return
  }
  connections.value = nextConnections
  connectionTotal.value = nextTotal
  connectionPageSize.value = nextPageSize
  Object.assign(connectionSummary, {
    total: pageData.summary?.total || 0,
    active: pageData.summary?.active || 0,
    attention: pageData.summary?.attention || 0,
    disabled: pageData.summary?.disabled || 0
  })
  connectionTotalCount.value = connectionSummary.total || connectionTotal.value
}

function resetConnectionList() {
  connections.value = []
  connectionTotal.value = 0
  connectionTotalCount.value = 0
  Object.assign(connectionSummary, {
    total: 0,
    active: 0,
    attention: 0,
    disabled: 0
  })
}

const {
  isActionLoading,
  updateConnectionStatus,
  testConnection,
  reauthorizeConnection,
  deleteConnection
} = useMcpConnectionActions({
  serverName: () => props.server?.name,
  reload: fetchConnections,
  getConnectionTitle,
  onDeleted: async (connection) => {
    if (editingConnectionId.value === connection.id) {
      closeForm()
    }
  }
})

const handleToggleConnectionStatus = (connection, checked) =>
  updateConnectionStatus(connection, checked, { canToggle: canToggleConnectionStatus })
const handleTestConnection = (connection) =>
  testConnection(connection, { canTest: canTestConnection })
const handleReauthorizeConnection = (connection) =>
  reauthorizeConnection(connection, { canReauthorize: canReauthorizeConnection })

const isIssueActionLoading = (connection) => {
  const issue = getConnectionIssue(connection)
  return issue?.key === 'reauth_required' && isActionLoading(connection, 'reauth')
}

const handleConnectionIssueAction = (connection) => {
  const issue = getConnectionIssue(connection)
  if (!issue) return
  if (issue.key === 'scope_mismatch') {
    openCreateForm()
    return
  }
  if (issue.key === 'missing_credentials' || issue.key === 'test_failed') {
    startEditForm(connection)
    return
  }
  if (issue.key === 'reauth_required') {
    handleReauthorizeConnection(connection)
  }
}

const resetConnectionFilters = () => {
  connectionFilter.value = 'all'
  connectionSearchText.value = ''
}

const handleSubmitConnection = async () => {
  if (!props.server) return

  const scopeId = form.scopeType === 'system' ? 'global' : form.scopeId.trim()
  if (!scopeId) {
    message.error(`${scopeIdLabel.value}不能为空`)
    return
  }
  if (!validateCredential()) return

  const payload = buildBasePayload()
  if (payload === undefined) return
  const credential = buildCredential({ isEditing: false })
  if (credential === undefined) return
  if (credential !== null) {
    payload.credential = credential
  }

  try {
    submitting.value = true
    const result = isEditing.value
      ? await mcpApi.updateMcpServerConnection(props.server.name, editingConnectionId.value, payload)
      : await mcpApi.createMcpServerConnection(props.server.name, {
          scope_type: form.scopeType,
          scope_id: scopeId,
          status: 'active',
          ...payload
        })
    if (result.success) {
      message.success(isEditing.value ? '连接更新成功' : '连接创建成功')
      closeForm()
      await fetchConnections()
    } else {
      message.error(result.message || (isEditing.value ? '连接更新失败' : '连接创建失败'))
    }
  } catch (err) {
    message.error(err.message || (isEditing.value ? '连接更新失败' : '连接创建失败'))
  } finally {
    submitting.value = false
  }
}

const loadScopeOptions = async () => {
  if (scopeOptionsLoaded.value) return
  try {
    isFetchingScopeOptions.value = true
    const [usersRes, deptsRes] = await Promise.all([
      userApi.getUsers(),
      departmentApi.getDepartments()
    ])
    userList.value = usersRes || []
    departmentList.value = deptsRes || []
    scopeOptionsLoaded.value = true
  } catch (err) {
    message.error('获取用户/部门列表失败: ' + err.message)
  } finally {
    isFetchingScopeOptions.value = false
  }
}

watch(
  () => [props.active, props.server?.name],
  ([active]) => {
    if (!active || !props.server) return
    loadScopeOptions()
    fetchConnections()
  },
  { immediate: true }
)

watch(connectionFilter, () => {
  if (!props.active) return
  if (connectionPage.value === 1) {
    fetchConnections()
  } else {
    connectionPage.value = 1
  }
})

watch(connectionSearchText, (_value, _oldValue, onCleanup) => {
  if (!props.active) return
  const timer = setTimeout(() => {
    if (connectionPage.value === 1) {
      fetchConnections()
    } else {
      connectionPage.value = 1
    }
  }, 300)
  onCleanup(() => clearTimeout(timer))
})

watch([connectionPage, connectionPageSize], () => {
  if (!props.active) return
  fetchConnections()
})
</script>

<style lang="less" scoped>
.connections-tab {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.connection-command-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-25);
}

.connection-command-copy {
  h3 {
    margin: 0 0 6px;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
  }

  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.5;
  }
}

.connection-summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  overflow: hidden;
  background: var(--gray-0);
}

.connection-summary-item {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;

  & + .connection-summary-item {
    border-left: 1px solid var(--gray-100);
  }

  .summary-label {
    color: var(--gray-500);
    font-size: 12px;
  }

  strong {
    color: var(--gray-900);
    font-size: 15px;
    font-weight: 600;
  }
}

.connection-list-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.connection-filter-group,
.connection-page-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.connection-filter-group {
  min-width: 0;
  flex: 1;
}

.connection-page-controls {
  flex-shrink: 0;
}

.connection-result-count {
  color: var(--gray-500);
  font-size: 12px;
}

.connection-search-input {
  max-width: 280px;
}

.connection-empty-state {
  min-height: 260px;
  padding: 32px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.connection-list-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.connection-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 12px;
}

.connection-pagination {
  display: flex;
  justify-content: flex-end;
}

.detail-empty {
  padding: 40px 0;
}

@media (max-width: 900px) {
  .connection-list-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .connection-filter-group,
  .connection-page-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .connection-search-input {
    max-width: none;
  }

  .connection-summary-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .connection-summary-item:nth-child(3) {
    border-left: none;
    border-top: 1px solid var(--gray-100);
  }

  .connection-summary-item:nth-child(4) {
    border-top: 1px solid var(--gray-100);
  }

  .connection-command-bar {
    align-items: stretch;
    flex-direction: column;
  }

  .connection-pagination {
    justify-content: flex-start;
  }

  .connection-cards-grid {
    grid-template-columns: 1fr;
  }
}
</style>
