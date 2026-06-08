<template>
  <div class="mcp-personal-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">MCP 连接</div>
        <p class="section-description">维护需要绑定到当前账号的 MCP 凭据。</p>
      </div>
      <a-button class="add-btn lucide-icon-btn" :loading="loading" @click="loadServers">
        <RefreshCw :size="14" />
        刷新
      </a-button>
    </div>

    <div class="mcp-personal-layout">
      <a-spin :spinning="loading">
        <div class="server-list">
          <a-empty v-if="servers.length === 0" description="暂无可用 MCP" />
          <template v-else>
            <button
              v-for="item in servers"
              :key="item.name"
              type="button"
              class="server-option"
              :class="{ active: selectedName === item.name }"
              @click="selectServer(item.name)"
            >
              <span class="server-icon">{{ item.icon || '🔌' }}</span>
              <span class="server-copy">
                <strong>{{ item.name }}</strong>
                <small>{{ item.description || '暂无描述' }}</small>
              </span>
            </button>
          </template>
        </div>
      </a-spin>

      <div class="server-detail">
        <a-spin :spinning="detailLoading || connectionsLoading">
          <div v-if="!selectedServer" class="detail-empty">
            <a-empty description="请选择 MCP" />
          </div>

          <template v-else>
            <div class="detail-header">
              <div class="detail-header-copy">
                <div class="detail-title">
                  <span>{{ selectedServer.icon || '🔌' }}</span>
                  <h3>{{ selectedServer.name }}</h3>
                </div>
                <p>{{ selectedServer.description || '暂无描述' }}</p>
              </div>
              <span
                v-if="bindingScopeLabel"
                class="binding-scope-badge"
                :class="`scope-${bindingScope || 'unknown'}`"
                :title="`生效范围：${bindingScopeLabel}`"
              >
                <Globe2 v-if="bindingScope === 'system'" :size="13" />
                <Building2 v-else-if="bindingScope === 'department'" :size="13" />
                <UserRound v-else :size="13" />
                {{ bindingScopeLabel }}
              </span>
            </div>

            <a-alert
              v-if="!canManagePersonalConnection"
              :type="selectedServer.auth_config?.provider ? 'info' : 'warning'"
              :message="personalUnavailableMessage"
              show-icon
            />

            <template v-else>
              <div v-if="selectedConnection && !showForm" class="connection-panel">
                <div class="card-header">
                  <div class="key-info">
                    <KeyRound size="18" class="key-icon" />
                    <div class="key-info-content">
                      <h4 class="key-name">
                        {{ selectedConnection.display_name || selectedServer.name }}
                      </h4>
                    </div>
                  </div>
                </div>

                <div class="card-content">
                  <div
                    v-if="getConnectionIssue(selectedConnection)"
                    class="connection-issue"
                    :class="`issue-${getConnectionIssue(selectedConnection).tone}`"
                  >
                    <div class="issue-copy">
                      <span>问题：{{ getConnectionIssue(selectedConnection).label }}</span>
                      <small>{{ getConnectionIssue(selectedConnection).description }}</small>
                    </div>
                    <a-button
                      type="link"
                      size="small"
                      class="issue-action"
                      :loading="
                        actionLoading ===
                        (getConnectionIssue(selectedConnection).key === 'reauth_required'
                          ? 'reauth'
                          : 'issue')
                      "
                      @click="handleConnectionIssueAction"
                    >
                      {{ getConnectionIssue(selectedConnection).actionLabel }}
                    </a-button>
                  </div>
                  <div class="info-item">
                    <span class="info-label">最近记录:</span>
                    <span class="info-value">
                      {{ getConnectionLastInfo(selectedConnection) }}
                    </span>
                  </div>
                </div>

                <div class="card-footer">
                  <div class="footer-left">
                    <span class="switch-label">
                      {{ isConnectionActive ? '已启用' : getStatusLabel(connectionStatus) }}
                    </span>
                    <a-tooltip :title="statusToggleTooltip">
                      <span class="status-switch-wrap">
                        <a-switch
                          size="small"
                          :checked="isConnectionActive"
                          :disabled="!canToggleConnectionStatus"
                          :loading="actionLoading === 'status'"
                          @change="updateConnectionEnabled"
                        />
                      </span>
                    </a-tooltip>
                  </div>
                  <div class="footer-actions">
                    <a-button
                      type="text"
                      size="small"
                      class="action-btn lucide-icon-btn"
                      @click="startEditConnection"
                    >
                      <Pencil :size="14" />
                      <span>编辑</span>
                    </a-button>
                    <a-button
                      type="text"
                      size="small"
                      class="action-btn lucide-icon-btn"
                      :loading="actionLoading === 'test'"
                      :disabled="!canTestConnection"
                      @click="testConnection"
                    >
                      <Zap :size="14" />
                      <span>测试</span>
                    </a-button>
                    <a-button
                      type="text"
                      size="small"
                      class="action-btn lucide-icon-btn"
                      :loading="actionLoading === 'reauth'"
                      :disabled="!canReauthorizeConnection"
                      @click="reauthorizeConnection"
                    >
                      <RotateCw :size="14" />
                      <span>重连</span>
                    </a-button>
                    <a-button
                      type="text"
                      size="small"
                      danger
                      class="action-btn danger-action-btn lucide-icon-btn"
                      @click="deleteConnection"
                    >
                      <Trash2 :size="14" />
                      <span>删除</span>
                    </a-button>
                  </div>
                </div>
              </div>

              <div v-else-if="!showForm" class="connection-empty">
                <a-empty description="暂无个人连接" />
                <a-button type="primary" class="lucide-icon-btn" @click="startCreateConnection">
                  <Plus :size="14" />
                  新建连接
                </a-button>
              </div>

              <div v-if="showForm" class="connection-form-panel">
                <div class="form-panel-header">
                  <h4>{{ isEditing ? '编辑连接' : '新建连接' }}</h4>
                  <a-button type="text" class="lucide-icon-btn" @click="cancelForm">
                    <X :size="14" />
                  </a-button>
                </div>

                <a-form layout="vertical" class="connection-form">
                  <div class="form-grid">
                    <a-form-item label="连接名称" class="full-width">
                      <a-input v-model:value="form.displayName" placeholder="例如：我的工作账号" />
                    </a-form-item>
                  </div>

                  <div v-if="secretFields.length > 0" class="secret-grid">
                    <a-form-item
                      v-for="fieldName in secretFields"
                      :key="fieldName"
                      :label="getSecretFieldLabel(fieldName)"
                    >
                      <a-input-password
                        v-model:value="form.secretValues[fieldName]"
                        :placeholder="isEditing ? '留空表示保持现有值' : `请输入 ${fieldName}`"
                      />
                    </a-form-item>
                  </div>

                  <a-form-item v-else label="长期凭据">
                    <a-textarea
                      v-model:value="form.credentialText"
                      :rows="4"
                      placeholder="粘贴长期 token"
                    />
                  </a-form-item>

                  <a-collapse ghost class="advanced-collapse">
                    <a-collapse-panel key="advanced" header="高级设置">
                      <a-form-item label="外部主体标识">
                        <a-input
                          v-model:value="form.externalSubject"
                          placeholder="可选，例如外部用户名"
                        />
                      </a-form-item>
                      <a-form-item v-if="secretFields.length > 0" label="原始凭据 JSON">
                        <a-textarea
                          v-model:value="form.credentialText"
                          :rows="4"
                          placeholder='可选，例如 {"secrets":{"access_token":"xxx"}}'
                        />
                      </a-form-item>
                      <a-form-item label="元数据 JSON">
                        <a-textarea
                          v-model:value="form.metaText"
                          :rows="3"
                          placeholder='可选，例如 {"tenant":"default"}'
                        />
                      </a-form-item>
                    </a-collapse-panel>
                  </a-collapse>

                  <div class="form-actions">
                    <a-button @click="cancelForm">取消</a-button>
                    <a-button
                      type="primary"
                      class="lucide-icon-btn"
                      :loading="submitting"
                      @click="submitConnection"
                    >
                      <Save :size="14" />
                      保存
                    </a-button>
                  </div>
                </a-form>
              </div>
            </template>
          </template>
        </a-spin>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  Building2,
  Globe2,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Trash2,
  UserRound,
  X,
  Zap
} from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import { formatFullDateTime } from '@/utils/time'
import { extractSecretFieldNames } from '@/utils/mcpAuthConfigBuilder'

const loading = ref(false)
const detailLoading = ref(false)
const connectionsLoading = ref(false)
const submitting = ref(false)
const actionLoading = ref(null)

const servers = ref([])
const selectedName = ref('')
const selectedServer = ref(null)
const connections = ref([])
const showForm = ref(false)
const editingConnectionId = ref(null)

const form = reactive({
  displayName: '',
  externalSubject: '',
  credentialText: '',
  secretValues: {},
  metaText: ''
})

const statusLabelMap = {
  active: '启用',
  disabled: '停用',
  reauth_required: '需要重连',
  invalid: '无效'
}

const scopeLabelMap = {
  system: '全局共享',
  department: '部门共享',
  user: '个人专用'
}

const selectedConnection = computed(() => connections.value[0] || null)
const isEditing = computed(() => editingConnectionId.value !== null)
const connectionStatus = computed(() => selectedConnection.value?.status || '')
const isConnectionActive = computed(() => connectionStatus.value === 'active')

const authConfig = computed(() => selectedServer.value?.auth_config || {})
const bindingScope = computed(() => authConfig.value.binding_scope || '')
const bindingScopeLabel = computed(() => scopeLabelMap[bindingScope.value] || '')
const canManagePersonalConnection = computed(() => bindingScope.value === 'user')

const secretFields = computed(() => {
  if (Array.isArray(authConfig.value.secret_fields)) {
    return authConfig.value.secret_fields
  }
  return extractSecretFieldNames(authConfig.value)
})

const connectionCredentialsRequired = computed(() => secretFields.value.length > 0)

const isConnectionCredentialMissing = (connection) =>
  connectionCredentialsRequired.value && !connection?.has_credentials

const canToggleConnectionStatus = computed(() => {
  if (!['active', 'disabled'].includes(connectionStatus.value)) return false
  if (connectionStatus.value === 'disabled') {
    return !isConnectionCredentialMissing(selectedConnection.value)
  }
  return true
})

const statusToggleTooltip = computed(() => {
  if (connectionStatus.value === 'active') return '停用连接'
  if (connectionStatus.value === 'disabled') {
    if (isConnectionCredentialMissing(selectedConnection.value)) return '请先补充凭据'
    return '启用连接'
  }
  return '请先重连或编辑凭据'
})

const canTestConnection = computed(
  () =>
    Boolean(selectedConnection.value) && !isConnectionCredentialMissing(selectedConnection.value)
)

const canReauthorizeConnection = computed(
  () =>
    Boolean(selectedConnection.value) && !isConnectionCredentialMissing(selectedConnection.value)
)

const personalUnavailableMessage = computed(() => {
  if (!authConfig.value.provider) {
    return '当前 MCP 未启用动态鉴权，无需配置个人连接。'
  }
  return '当前 MCP 使用共享连接，由管理员维护。'
})

const getSecretFieldLabel = (fieldName) => {
  const labelMap = {
    client_id: 'Client ID',
    client_secret: 'Client Secret',
    access_token: 'Access Token',
    refresh_token: 'Refresh Token',
    issuer_url: 'Issuer URL',
    api_key: 'API Key'
  }
  return labelMap[fieldName] || fieldName
}

const getStatusLabel = (status) => statusLabelMap[status] || status || '未知状态'

const getConnectionIssue = (connection) => {
  if (!connection) return null
  if (isConnectionCredentialMissing(connection)) {
    return {
      key: 'missing_credentials',
      label: '缺少凭据',
      description: '缺少长期凭据，当前账号无法使用该 MCP。',
      actionLabel: '补充凭据',
      tone: 'error'
    }
  }
  if (connection.status === 'reauth_required') {
    return {
      key: 'reauth_required',
      label: '授权失效',
      description: '授权缓存已失效，需要重新连接后继续使用。',
      actionLabel: '重连',
      tone: 'warning'
    }
  }
  if (connection.status === 'invalid' || connection.meta_json?.last_error?.message) {
    return {
      key: 'test_failed',
      label: '测试失败',
      description: connection.meta_json?.last_error?.message || '最近一次连接检测失败。',
      actionLabel: '编辑凭据',
      tone: 'error'
    }
  }
  return null
}

const getConnectionLastInfo = (connection) => {
  if (connection.meta_json?.last_success_at) {
    return `最近成功 ${formatFullDateTime(connection.meta_json.last_success_at)}`
  }
  if (connection.updated_at) {
    return `更新于 ${formatFullDateTime(connection.updated_at)}`
  }
  return '暂无记录'
}

const resetForm = () => {
  editingConnectionId.value = null
  Object.assign(form, {
    displayName: '',
    externalSubject: '',
    credentialText: '',
    secretValues: Object.fromEntries(secretFields.value.map((fieldName) => [fieldName, ''])),
    metaText: ''
  })
}

const parseJsonText = (text, label, { allowRawString = false } = {}) => {
  const trimmed = String(text || '').trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    if (allowRawString) {
      return trimmed
    }
    message.error(`${label} JSON 格式错误`)
    return undefined
  }
}

const setNestedSecretValue = (target, path, value) => {
  const segments = String(path || '')
    .split('.')
    .filter(Boolean)
  let current = target
  segments.forEach((segment, index) => {
    if (index === segments.length - 1) {
      current[segment] = value
      return
    }
    current[segment] = current[segment] || {}
    current = current[segment]
  })
}

const buildCredential = () => {
  const rawCredential = parseJsonText(form.credentialText, '原始凭据', {
    allowRawString: true
  })
  if (rawCredential === undefined) return undefined
  if (rawCredential !== null) return rawCredential

  const secrets = {}
  Object.entries(form.secretValues).forEach(([key, value]) => {
    const trimmedValue = String(value || '').trim()
    if (trimmedValue) {
      setNestedSecretValue(secrets, key, trimmedValue)
    }
  })

  if (Object.keys(secrets).length === 0) {
    return isEditing.value ? undefined : null
  }
  return { secrets }
}

const validateCredential = () => {
  if (isEditing.value || secretFields.value.length === 0 || form.credentialText.trim()) {
    return true
  }
  const missingFields = secretFields.value.filter(
    (fieldName) => !String(form.secretValues[fieldName] || '').trim()
  )
  if (missingFields.length === 0) {
    return true
  }
  message.error(`请填写凭据字段：${missingFields.join('、')}`)
  return false
}

const loadServers = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServers()
    const nextServers = (result.data || [])
      .filter((item) => item.enabled !== false)
      .sort((a, b) =>
        String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', {
          sensitivity: 'base',
          numeric: true
        })
      )
    servers.value = nextServers
    if (!nextServers.some((item) => item.name === selectedName.value)) {
      selectedName.value = ''
      selectedServer.value = null
      connections.value = []
      if (nextServers[0]) {
        await selectServer(nextServers[0].name)
      }
    } else if (selectedName.value) {
      await loadSelectedServer()
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 列表失败')
  } finally {
    loading.value = false
  }
}

const loadSelectedServer = async () => {
  if (!selectedName.value) return
  try {
    detailLoading.value = true
    const result = await mcpApi.getMcpServer(selectedName.value)
    selectedServer.value = result.data || null
    resetForm()
    showForm.value = false
    if (canManagePersonalConnection.value) {
      await loadConnections()
    } else {
      connections.value = []
    }
  } catch (err) {
    selectedServer.value = null
    connections.value = []
    message.error(err.message || '获取 MCP 详情失败')
  } finally {
    detailLoading.value = false
  }
}

const loadConnections = async () => {
  if (!selectedName.value) return
  try {
    connectionsLoading.value = true
    const result = await mcpApi.getMcpServerConnections(selectedName.value, { mine: true })
    connections.value = result.data || []
  } catch (err) {
    connections.value = []
    message.error(err.message || '获取连接失败')
  } finally {
    connectionsLoading.value = false
  }
}

const selectServer = async (serverName) => {
  if (!serverName || selectedName.value === serverName) return
  selectedName.value = serverName
  await loadSelectedServer()
}

const startCreateConnection = () => {
  resetForm()
  showForm.value = true
}

const startEditConnection = () => {
  if (!selectedConnection.value) return
  editingConnectionId.value = selectedConnection.value.id
  Object.assign(form, {
    displayName: selectedConnection.value.display_name || '',
    externalSubject: selectedConnection.value.external_subject || '',
    credentialText: '',
    secretValues: Object.fromEntries(secretFields.value.map((fieldName) => [fieldName, ''])),
    metaText: selectedConnection.value.meta_json
      ? JSON.stringify(selectedConnection.value.meta_json, null, 2)
      : ''
  })
  showForm.value = true
}

const cancelForm = () => {
  showForm.value = false
  resetForm()
}

const submitConnection = async () => {
  if (!selectedServer.value || !validateCredential()) return
  const metaJson = parseJsonText(form.metaText, '元数据')
  if (metaJson === undefined) return
  const credential = buildCredential()
  if (credential === undefined && !isEditing.value) return

  try {
    submitting.value = true
    const payload = {
      display_name: form.displayName || null,
      external_subject: form.externalSubject || null,
      meta_json: metaJson
    }
    if (credential !== undefined && credential !== null) {
      payload.credential = credential
    }

    const result = isEditing.value
      ? await mcpApi.updateMcpServerConnection(
          selectedServer.value.name,
          editingConnectionId.value,
          payload
        )
      : await mcpApi.createMcpServerConnection(selectedServer.value.name, {
          scope_type: 'user',
          ...payload
        })

    if (result.success) {
      message.success(isEditing.value ? '连接已更新' : '连接已创建')
      showForm.value = false
      resetForm()
      await loadConnections()
    } else {
      message.error(result.message || '保存失败')
    }
  } catch (err) {
    message.error(err.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

const updateConnectionEnabled = async (checked) => {
  if (!selectedConnection.value || !selectedServer.value || !canToggleConnectionStatus.value) return
  const nextStatus = checked ? 'active' : 'disabled'
  if (selectedConnection.value.status === nextStatus) return
  try {
    actionLoading.value = 'status'
    const result = await mcpApi.updateMcpConnectionStatus(
      selectedServer.value.name,
      selectedConnection.value.id,
      nextStatus
    )
    message.success(result.message || (checked ? '连接已启用' : '连接已停用'))
    await loadConnections()
  } catch (err) {
    message.error(err.message || '状态更新失败')
    await loadConnections()
  } finally {
    actionLoading.value = null
  }
}

const handleConnectionIssueAction = () => {
  const issue = getConnectionIssue(selectedConnection.value)
  if (!issue) return
  if (issue.key === 'missing_credentials' || issue.key === 'test_failed') {
    startEditConnection()
    return
  }
  if (issue.key === 'reauth_required') {
    reauthorizeConnection()
  }
}

const testConnection = async () => {
  if (!selectedConnection.value || !selectedServer.value || !canTestConnection.value) return
  try {
    actionLoading.value = 'test'
    const result = await mcpApi.testMcpConnection(
      selectedServer.value.name,
      selectedConnection.value.id
    )
    message.success(result.message || '连接测试成功')
    await loadConnections()
  } catch (err) {
    message.error(err.message || '连接测试失败')
  } finally {
    actionLoading.value = null
  }
}

const reauthorizeConnection = async () => {
  if (!selectedConnection.value || !selectedServer.value || !canReauthorizeConnection.value) return
  try {
    actionLoading.value = 'reauth'
    const result = await mcpApi.reauthorizeMcpConnection(
      selectedServer.value.name,
      selectedConnection.value.id
    )
    message.success(result.message || '连接已重置')
    await loadConnections()
  } catch (err) {
    message.error(err.message || '连接重置失败')
  } finally {
    actionLoading.value = null
  }
}

const deleteConnection = () => {
  if (!selectedConnection.value || !selectedServer.value) return
  Modal.confirm({
    title: '确认删除连接',
    content: `确定要删除 "${selectedConnection.value.display_name || selectedServer.value.name}" 吗？`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await mcpApi.deleteMcpServerConnection(
          selectedServer.value.name,
          selectedConnection.value.id
        )
        message.success('连接已删除')
        showForm.value = false
        resetForm()
        await loadConnections()
      } catch (err) {
        message.error(err.message || '连接删除失败')
      }
    }
  })
}

onMounted(() => {
  loadServers()
})
</script>

<style lang="less" scoped>
.mcp-personal-settings {
  .mcp-personal-layout {
    display: grid;
    grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
    gap: 14px;
    min-height: 420px;
  }

  .server-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 360px;
    padding-right: 4px;
    border-right: 1px solid var(--gray-150);
  }

  .server-option {
    width: 100%;
    min-height: 64px;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 10px;
    background: transparent;
    color: var(--gray-800);
    display: flex;
    gap: 10px;
    text-align: left;
    cursor: pointer;

    &:hover,
    &.active {
      background: var(--gray-50);
      border-color: var(--gray-150);
    }

    &.active {
      color: var(--main-700);
    }
  }

  .server-icon {
    width: 28px;
    height: 28px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    font-size: 18px;
    line-height: 1;
  }

  .server-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;

    strong,
    small {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    strong {
      color: inherit;
      font-size: 14px;
      font-weight: 600;
    }

    small {
      color: var(--gray-600);
      font-size: 12px;
    }
  }

  .server-detail {
    min-width: 0;
    min-height: 360px;
  }

  .detail-empty,
  .connection-empty {
    min-height: 320px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
  }

  .detail-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;

    .detail-header-copy {
      min-width: 0;
    }

    p {
      margin: 6px 0 0;
      color: var(--gray-600);
      font-size: 13px;
      line-height: 1.5;
    }
  }

  .detail-title {
    display: flex;
    align-items: center;
    gap: 8px;

    h3 {
      margin: 0;
      min-width: 0;
      overflow: hidden;
      color: var(--gray-900);
      font-size: 17px;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .binding-scope-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 28px;
    flex-shrink: 0;
    gap: 5px;
    padding: 0 10px;
    border: 1px solid var(--gray-150);
    border-radius: 7px;
    background: var(--gray-25);
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;

    &.scope-system {
      border-color: var(--color-success-100);
      background: var(--color-success-50);
      color: var(--color-success-700);
    }

    &.scope-department {
      border-color: var(--color-accent-100);
      background: var(--color-accent-50);
      color: var(--color-accent-700);
    }

    &.scope-user {
      border-color: var(--color-info-100);
      background: var(--color-info-50);
      color: var(--color-info-700);
    }
  }

  .connection-panel,
  .connection-form-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  .connection-panel {
    padding: 12px;
    transition:
      border-color 0.2s,
      box-shadow 0.2s;

    &:hover {
      border-color: var(--gray-300);
    }
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }

  .key-info {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .key-icon {
    color: var(--main-600);
    flex-shrink: 0;
  }

  .key-info-content {
    min-width: 0;
  }

  .key-name {
    margin: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
    line-height: 1.4;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .card-content {
    margin-bottom: 10px;
  }

  .connection-issue {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
    padding: 9px 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-25);

    &.issue-warning {
      border-color: var(--color-warning-100);
      background: var(--color-warning-10);

      .issue-copy span {
        color: var(--color-warning-900);
      }
    }

    &.issue-error {
      border-color: var(--color-error-100);
      background: var(--color-error-10);

      .issue-copy span {
        color: var(--color-error-700);
      }
    }
  }

  .issue-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;

    span {
      font-size: 13px;
      font-weight: 600;
      line-height: 1.4;
    }

    small {
      overflow: hidden;
      color: var(--gray-600);
      font-size: 12px;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .issue-action {
    flex-shrink: 0;
    padding: 0;
    font-size: 12px;
    font-weight: 500;
  }

  .info-item {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    margin-bottom: 6px;
    color: var(--gray-900);
    font-size: 13px;

    &:last-child {
      margin-bottom: 0;
    }
  }

  .info-label {
    color: var(--gray-600);
    flex-shrink: 0;
  }

  .info-value {
    min-width: 0;
    color: var(--gray-900);
    word-break: break-all;
  }

  .card-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--gray-100);
  }

  .footer-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .switch-label {
    color: var(--gray-600);
    font-size: 12px;
  }

  .status-switch-wrap {
    display: inline-flex;
    align-items: center;
  }

  .footer-actions {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
    flex-wrap: wrap;
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--gray-700);
    font-size: 12px;

    &:hover {
      color: var(--main-600);
    }
  }

  .danger-action-btn {
    color: var(--color-error-700);

    &:hover {
      background: var(--color-error-50);
      color: var(--color-error-900);
    }
  }

  .connection-form-panel {
    overflow: hidden;
  }

  .form-panel-header {
    height: 48px;
    padding: 0 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--gray-150);

    h4 {
      margin: 0;
      color: var(--gray-900);
      font-size: 15px;
      font-weight: 600;
    }
  }

  .connection-form {
    padding: 14px;
  }

  .form-grid,
  .secret-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }

  .form-grid {
    .full-width {
      grid-column: 1 / -1;
    }
  }

  .advanced-collapse {
    margin-top: 2px;
  }

  .form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 8px;
  }

  @media (max-width: 900px) {
    .mcp-personal-layout {
      grid-template-columns: 1fr;
    }

    .server-list {
      min-height: 0;
      max-height: 220px;
      overflow-y: auto;
      border-right: none;
      border-bottom: 1px solid var(--gray-150);
      padding: 0 0 10px;
    }

    .form-grid,
    .secret-grid {
      grid-template-columns: 1fr;
      gap: 0;
    }

    .connection-issue {
      align-items: flex-start;
      flex-direction: column;
    }
  }
}
</style>
