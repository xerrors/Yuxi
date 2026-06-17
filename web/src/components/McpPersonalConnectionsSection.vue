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
              <McpConnectionCard
                v-if="selectedConnection && !isOpen"
                :connection="selectedConnection"
                :title="getConnectionTitle(selectedConnection)"
                :subtitle="selectedConnection.external_subject || ''"
                :show-scope-badge="false"
                :issue="getConnectionIssue(selectedConnection)"
                :last-info="getConnectionLastInfo(selectedConnection)"
                :status-switch-label="getConnectionStatusSwitchLabel(selectedConnection)"
                :status-toggle-tooltip="getConnectionStatusToggleTooltip(selectedConnection)"
                :test-tooltip="getConnectionTestTooltip(selectedConnection)"
                :reauthorize-tooltip="getConnectionReauthorizeTooltip(selectedConnection)"
                :can-toggle-status="canToggleConnectionStatus(selectedConnection)"
                :can-test="canTestConnection(selectedConnection)"
                :can-reauthorize="canReauthorizeConnection(selectedConnection)"
                :status-loading="isActionLoading(selectedConnection, 'status')"
                :test-loading="isActionLoading(selectedConnection, 'test')"
                :reauthorize-loading="isActionLoading(selectedConnection, 'reauth')"
                :issue-loading="isIssueActionLoading(selectedConnection)"
                variant="panel"
                @edit="startEditForm"
                @test="handleTestConnection"
                @reauthorize="handleReauthorizeConnection"
                @delete="deleteConnection"
                @toggle-status="handleToggleConnectionStatus"
                @issue-action="handleConnectionIssueAction"
              />

              <div v-else-if="!isOpen" class="connection-empty">
                <a-empty description="暂无个人连接" />
                <a-button type="primary" class="lucide-icon-btn" @click="openCreateForm">
                  <Plus :size="14" />
                  新建连接
                </a-button>
              </div>

              <McpConnectionForm
                v-if="isOpen"
                v-model="form"
                :title="isEditing ? '编辑连接' : '新建连接'"
                variant="panel"
                :is-editing="isEditing"
                :submitting="submitting"
                :secret-fields="secretFields"
                :credential-hint="credentialHint"
                display-name-placeholder="例如：我的工作账号"
                external-subject-placeholder="可选，例如外部用户名"
                raw-credential-placeholder="粘贴长期 token"
                :advanced-credential-rows="4"
                :meta-rows="3"
                submit-text="保存"
                @submit="handleSubmitConnection"
                @cancel="closeForm"
              />
            </template>
          </template>
        </a-spin>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, shallowRef } from 'vue'
import { message } from 'ant-design-vue'
import { Building2, Globe2, Plus, RefreshCw, UserRound } from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import { useMcpConnectionActions } from '@/composables/useMcpConnectionActions'
import { useMcpConnectionCardState } from '@/composables/useMcpConnectionCardState'
import { useMcpConnectionForm } from '@/composables/useMcpConnectionForm'
import McpConnectionCard from '@/components/mcp/McpConnectionCard.vue'
import McpConnectionForm from '@/components/mcp/McpConnectionForm.vue'
import { extractSecretFieldNames } from '@/utils/mcpAuthConfigBuilder'
import {
  MCP_CONNECTION_SCOPE_LABELS,
  isMcpConnectionCredentialMissing
} from '@/utils/mcpConnectionUtils'

const loading = shallowRef(false)
const detailLoading = shallowRef(false)
const connectionsLoading = shallowRef(false)

const servers = shallowRef([])
const selectedName = shallowRef('')
const selectedServer = shallowRef(null)
const connections = shallowRef([])

const scopeLabelMap = MCP_CONNECTION_SCOPE_LABELS

const selectedConnection = computed(() => connections.value[0] || null)
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
const personalUnavailableMessage = computed(() => {
  if (!authConfig.value.provider) {
    return '当前 MCP 未启用动态鉴权，无需配置个人连接。'
  }
  return '当前 MCP 使用共享连接，由管理员维护。'
})

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
  secretFields,
  defaultScopeType: 'user',
  onError: message.error
})

const credentialHint = computed(() => {
  if (isEditing.value) {
    return '为安全起见不回显已有凭据；留空表示保持原值。'
  }
  if (secretFields.value.length > 0) {
    return '系统已根据认证配置推导出需要录入的密钥字段。'
  }
  return '当前认证配置没有声明密钥字段，可直接粘贴长期 token。'
})

const isConnectionCredentialMissing = (connection) =>
  isMcpConnectionCredentialMissing(connection, connectionCredentialsRequired.value)
const getConnectionTitle = (connection) =>
  connection?.display_name || selectedServer.value?.name || '个人连接'
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
  includeScopeMismatch: false,
  isCredentialMissing: isConnectionCredentialMissing,
  missingCredentialsDescription: '缺少长期凭据，当前账号无法使用该 MCP。',
  reauthRequiredDescription: '授权缓存已失效，需要重新连接后继续使用。'
})

const {
  isActionLoading,
  updateConnectionStatus,
  testConnection,
  reauthorizeConnection,
  deleteConnection
} = useMcpConnectionActions({
  serverName: () => selectedServer.value?.name,
  reload: loadConnections,
  getConnectionTitle,
  onDeleted: async () => {
    closeForm()
  }
})

async function loadServers() {
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

async function loadSelectedServer() {
  if (!selectedName.value) return
  try {
    detailLoading.value = true
    const result = await mcpApi.getMcpServer(selectedName.value)
    selectedServer.value = result.data || null
    closeForm()
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

async function loadConnections() {
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

async function selectServer(serverName) {
  if (!serverName || selectedName.value === serverName) return
  selectedName.value = serverName
  await loadSelectedServer()
}

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
  if (issue.key === 'missing_credentials' || issue.key === 'test_failed') {
    startEditForm(connection)
    return
  }
  if (issue.key === 'reauth_required') {
    handleReauthorizeConnection(connection)
  }
}

async function handleSubmitConnection() {
  if (!selectedServer.value || !validateCredential()) return
  const payload = buildBasePayload('元数据')
  if (payload === undefined) return
  const credential = buildCredential({ emptyCreateValue: null, rawLabel: '原始凭据' })
  if (credential === undefined && !isEditing.value) return
  if (credential !== undefined && credential !== null) {
    payload.credential = credential
  }

  try {
    submitting.value = true
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
      closeForm()
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
  }
}
</style>
