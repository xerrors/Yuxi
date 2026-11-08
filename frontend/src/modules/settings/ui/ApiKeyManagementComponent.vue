<template>
  <div class="apikey-management settings-page">
    <!-- 头部区域 -->
    <div class="settings-header">
      <div class="settings-header-copy">
        <div class="settings-section-title">API Key 管理</div>
        <p class="settings-section-description">
          用于外部系统调用 Agent 对话或知识库查询接口。密钥仅显示一次，请妥善保管。
        </p>
      </div>
      <div class="settings-header-actions">
        <a-button
          @click="handleRefresh"
          :loading="refreshing"
          title="刷新"
          class="yuxi-refresh-button lucide-icon-btn"
        >
          <template #icon><RefreshCw :size="16" :class="{ 'yuxi-rotate': refreshing }" /></template>
        </a-button>
        <a-button type="primary" @click="showCreateModal" class="lucide-icon-btn">
          <Plus :size="14" />
          创建 API Key
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="settings-management-content">
      <a-spin :spinning="loading">
        <div v-if="error" class="settings-error">
          <a-alert type="error" :message="error" show-icon />
        </div>

        <template v-if="apiKeys.length > 0">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="apiKeys"
              :columns="columns"
              :rowKey="(record) => record.id"
              :pagination="false"
              :scroll="{ x: 1040 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'name'">
                  <div class="settings-table-title">
                    <KeyIcon :size="15" class="settings-table-icon" />
                    <span class="settings-table-name" :title="record.name">{{ record.name }}</span>
                  </div>
                </template>
                <template v-if="column.key === 'prefix'">
                  <code class="code-badge">{{ record.key_prefix }}****</code>
                </template>
                <template v-if="column.key === 'access'">
                  <span>{{ accessLevelLabel(record.access_level) }}</span>
                </template>
                <template v-if="column.key === 'app'">
                  <span class="time-text">{{
                    record.app_id || (record.access_level === 'knowledge' ? '未设置' : '本人身份')
                  }}</span>
                </template>
                <template v-if="column.key === 'status'">
                  <div class="status-cell">
                    <a-switch
                      :checked="record.is_enabled"
                      size="small"
                      @change="toggleEnabled(record)"
                    />
                  </div>
                </template>
                <template v-if="column.key === 'lastUsed'">
                  <span class="time-text">{{ formatTime(record.last_used_at) }}</span>
                </template>
                <template v-if="column.key === 'expiresAt'">
                  <span class="time-text">{{ record.expires_at || '永不过期' }}</span>
                </template>
                <template v-if="column.key === 'action'">
                  <a-tooltip title="调整权限和来源">
                    <a-button type="text" size="small" class="yuxi-table-action lucide-icon-btn" @click="openEdit(record)">
                      <Pencil :size="14" />
                    </a-button>
                  </a-tooltip>
                  <a-popconfirm
                    title="确定要删除此 API Key 吗？此操作不可恢复。"
                    @confirm="deleteKey(record)"
                    ok-text="确定"
                    cancel-text="取消"
                  >
                    <a-tooltip title="删除 API Key">
                      <a-button type="text" size="small" danger class="yuxi-table-action lucide-icon-btn">
                        <Trash2 :size="14" />
                      </a-button>
                    </a-tooltip>
                  </a-popconfirm>
                </template>
              </template>
            </a-table>
          </div>
        </template>

        <div v-else class="settings-empty">
          <a-empty description="暂无 API Key，点击上方按钮创建一个" />
        </div>
      </a-spin>
    </div>

    <!-- 创建 Modal -->
    <a-modal
      v-model:open="createModalVisible"
      title="创建 API Key"
      @ok="handleCreate"
      @cancel="handleCreateCancel"
      :confirmLoading="createLoading"
      ok-text="创建"
      cancel-text="取消"
    >
      <a-form layout="vertical" :model="createForm">
        <a-form-item label="名称" required>
          <a-input v-model:value="createForm.name" placeholder="如：生产环境API" />
        </a-form-item>
        <a-form-item label="访问权限" required>
          <a-select v-model:value="createForm.access_level">
            <a-select-option value="agents">仅 Agents Public API</a-select-option>
            <a-select-option value="knowledge">仅 Knowledge Public API</a-select-option>
            <a-select-option value="full">完整访问（含产品内接口）</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item v-if="createForm.access_level !== 'knowledge'" label="资源身份" required>
          <a-radio-group v-model:value="createForm.identity_scope" aria-label="资源身份">
            <a-radio value="self">本人身份</a-radio>
            <a-radio value="app">APP 终端用户身份</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item
          v-if="createForm.access_level === 'knowledge' || createForm.identity_scope === 'app'"
          label="APP 标识"
          :required="createForm.access_level !== 'knowledge'"
        >
          <a-input
            v-model:value="createForm.app_id"
            :maxlength="64"
            placeholder="如：customer-service"
          />
        </a-form-item>
        <a-alert
          v-if="createForm.access_level === 'agents'"
          type="info"
          :message="
            createForm.identity_scope === 'self'
              ? '可访问本人已有的可见会话和工作区，并通过 Agent 使用已授权工具；不限于此 Key 新建的会话，也不是只读凭据。'
              : '会话和工作区属于 APP 的独立终端用户；未声明终端用户时使用该 APP 的默认终端用户。'
          "
          show-icon
          class="scope-notice"
        />
        <a-alert
          v-if="createForm.access_level === 'full'"
          type="warning"
          message="完整访问允许这把 Key 调用产品内接口。"
          show-icon
        />
        <a-form-item label="过期时间">
          <a-date-picker
            v-model:value="createForm.expires_at"
            show-time
            placeholder="留空表示永不过期"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="editModalVisible"
      title="调整 API Key 权限"
      ok-text="保存"
      :confirmLoading="editLoading"
      @ok="handleEdit"
    >
      <a-form layout="vertical" :model="editForm">
        <a-alert
          v-if="editScopeChanged"
          type="warning"
          :message="
            editAppId
              ? `保存后切换到 APP「${editAppId}」的终端用户资源`
              : '保存后切换到 Key 所属用户的本人资源'
          "
          :description="`当前身份：${editingKey.app_id ? `APP「${editingKey.app_id}」终端用户` : '本人'}。切换立即改变此 Key 可访问的会话和工作区范围，不移动历史数据。`"
          show-icon
          class="scope-notice"
        />
        <a-form-item label="访问权限" required>
          <a-select v-model:value="editForm.access_level">
            <a-select-option value="agents">仅 Agents Public API</a-select-option>
            <a-select-option value="knowledge">仅 Knowledge Public API</a-select-option>
            <a-select-option value="full">完整访问（含产品内接口）</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item v-if="editForm.access_level !== 'knowledge'" label="资源身份" required>
          <a-radio-group v-model:value="editForm.identity_scope" aria-label="资源身份">
            <a-radio value="self">本人身份</a-radio>
            <a-radio value="app">APP 终端用户身份</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item
          v-if="editForm.access_level === 'knowledge' || editForm.identity_scope === 'app'"
          label="APP 标识"
          :required="editForm.access_level !== 'knowledge'"
        >
          <a-input
            v-model:value="editForm.app_id"
            :maxlength="64"
            placeholder="如：customer-service"
          />
        </a-form-item>
        <a-alert
          v-if="editForm.access_level === 'agents'"
          type="info"
          :message="
            editForm.identity_scope === 'self'
              ? '可访问本人已有的可见会话和工作区，并通过 Agent 使用已授权工具；不限于此 Key 新建的会话，也不是只读凭据。'
              : '会话和工作区属于 APP 的独立终端用户；未声明终端用户时使用该 APP 的默认终端用户。'
          "
          show-icon
          class="scope-notice"
        />
        <a-alert
          v-if="editForm.access_level === 'full'"
          type="warning"
          message="完整访问允许这把 Key 调用产品内接口；保存后立即生效。"
          show-icon
        />
      </a-form>
    </a-modal>

    <!-- 密钥显示 Modal (创建后一次性显示) -->
    <a-modal
      v-model:open="secretModalVisible"
      title="API Key 已创建"
      :closable="true"
      @cancel="secretModalVisible = false"
      :footer="null"
      width="520px"
    >
      <div class="secret-display">
        <a-alert
          type="warning"
          message="请立即复制密钥，关闭后将无法再次查看完整密钥"
          show-icon
          class="secret-alert"
        />
        <div class="secret-value-container">
          <code class="secret-value">{{ createdSecret }}</code>
          <a-button type="primary" @click="copySecret" class="copy-btn lucide-icon-btn">
            <Copy :size="14" />
            复制
          </a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, Copy, Pencil } from '@lucide/vue'
import { Key as KeyIcon } from '@lucide/vue'
import { apikeyApi } from '@/apis/apikey_api'
import { copyTextToClipboard } from '@/shared/lib/clipboard'

const loading = ref(false)
const refreshing = ref(false)
const error = ref(null)
const apiKeys = ref([])

const createModalVisible = ref(false)
const secretModalVisible = ref(false)
const createLoading = ref(false)
const editModalVisible = ref(false)
const editLoading = ref(false)
const editingKey = ref(null)
const createdSecret = ref('')
const createRequestId = ref('')
const CREATE_REQUEST_STORAGE_KEY = 'yuxi_pending_api_key_request_id'

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 180 },
  { title: '前缀', dataIndex: 'key_prefix', key: 'prefix', width: 140 },
  { title: '权限', dataIndex: 'access_level', key: 'access', width: 130 },
  { title: '身份 / APP', dataIndex: 'app_id', key: 'app', width: 150 },
  { title: '状态', dataIndex: 'is_enabled', key: 'status', width: 80 },
  { title: '最后使用', dataIndex: 'last_used_at', key: 'lastUsed', width: 140 },
  { title: '过期时间', dataIndex: 'expires_at', key: 'expiresAt', width: 130 },
  { title: '操作', key: 'action', width: 90, align: 'center' }
]

const createForm = reactive({
  name: '',
  expires_at: null,
  access_level: 'agents',
  identity_scope: 'self',
  app_id: ''
})
const editForm = reactive({ access_level: 'agents', identity_scope: 'self', app_id: '' })
const createAppId = computed(() =>
  createForm.access_level === 'knowledge' || createForm.identity_scope === 'app'
    ? createForm.app_id || null
    : null
)
const editAppId = computed(() =>
  editForm.access_level === 'knowledge' || editForm.identity_scope === 'app'
    ? editForm.app_id || null
    : null
)
const editScopeChanged = computed(
  () =>
    editingKey.value &&
    editForm.access_level !== 'knowledge' &&
    editAppId.value !== editingKey.value.app_id
)
const APP_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/

const accessLevelLabel = (level) => ({
  agents: '仅 Agents API',
  knowledge: '仅 Knowledge Public API',
  full: '完整访问'
}[level] || level)

const validAppId = (appId, required) => {
  if (!appId) return !required
  return appId.length <= 64 && APP_ID_PATTERN.test(appId)
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const loadApiKeys = async () => {
  loading.value = true
  error.value = null
  try {
    const res = await apikeyApi.list()
    apiKeys.value = res.api_keys || []
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

// 刷新 API Key 列表
const handleRefresh = async () => {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadApiKeys()
    message.success('刷新成功')
  } catch (e) {
    console.error('刷新失败:', e)
    message.error('刷新失败')
  } finally {
    refreshing.value = false
  }
}

const showCreateModal = () => {
  createForm.name = ''
  createForm.expires_at = null
  createForm.access_level = 'agents'
  createForm.identity_scope = 'self'
  createForm.app_id = ''
  createRequestId.value =
    sessionStorage.getItem(CREATE_REQUEST_STORAGE_KEY) ||
    Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), (byte) =>
      byte.toString(16).padStart(2, '0')
    ).join('')
  sessionStorage.setItem(CREATE_REQUEST_STORAGE_KEY, createRequestId.value)
  createModalVisible.value = true
}

const handleCreateCancel = () => {
  sessionStorage.removeItem(CREATE_REQUEST_STORAGE_KEY)
  createRequestId.value = ''
}

const handleCreate = async () => {
  if (!createForm.name.trim()) {
    message.error('请输入名称')
    return
  }
  if (
    !validAppId(
      createAppId.value,
      createForm.access_level !== 'knowledge' && createForm.identity_scope === 'app'
    )
  ) {
    message.error('来源须为 1–64 位字母、数字、点、下划线、冒号或短横线，且以字母或数字开头')
    return
  }

  createLoading.value = true
  try {
    const data = {
      name: createForm.name,
      request_id: createRequestId.value,
      access_level: createForm.access_level,
      app_id: createAppId.value
    }
    if (createForm.expires_at) {
      data.expires_at = createForm.expires_at.format('YYYY-MM-DDTHH:mm:ss')
    }

    const res = await apikeyApi.create(data)
    sessionStorage.removeItem(CREATE_REQUEST_STORAGE_KEY)
    createRequestId.value = ''
    createdSecret.value = res.secret
    createModalVisible.value = false
    secretModalVisible.value = true
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}

const openEdit = (key) => {
  editingKey.value = key
  editForm.access_level = key.access_level
  editForm.app_id = key.app_id || ''
  editForm.identity_scope = key.app_id ? 'app' : 'self'
  editModalVisible.value = true
}

const handleEdit = async () => {
  if (!editingKey.value) return
  if (
    !validAppId(
      editAppId.value,
      editForm.access_level !== 'knowledge' && editForm.identity_scope === 'app'
    )
  ) {
    message.error('请填写有效的调用来源')
    return
  }
  editLoading.value = true
  try {
    await apikeyApi.update(editingKey.value.id, {
      access_level: editForm.access_level,
      app_id: editAppId.value
    })
    editModalVisible.value = false
    message.success('权限已更新')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '更新失败')
  } finally {
    editLoading.value = false
  }
}

const copySecret = async () => {
  try {
    await copyTextToClipboard(createdSecret.value)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const toggleEnabled = async (key) => {
  try {
    await apikeyApi.update(key.id, { is_enabled: !key.is_enabled })
    message.success(key.is_enabled ? '已禁用' : '已启用')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '操作失败')
  }
}

const deleteKey = async (key) => {
  try {
    await apikeyApi.delete(key.id)
    message.success('删除成功')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '删除失败')
  }
}

onMounted(() => {
  loadApiKeys()
})
</script>

<style lang="less" scoped>
.scope-notice {
  margin-bottom: 16px;
}

.apikey-management {
  .code-badge {
    font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
    font-size: 12px;
    color: var(--gray-700);
    background: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 4px;
    padding: 2px 6px;
    letter-spacing: 0.5px;
  }

  .status-cell {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .time-text {
    color: var(--gray-500);
    font-size: 12px;
  }
}

.secret-display {
  .secret-alert {
    margin-bottom: 16px;
  }

  .secret-value-container {
    display: flex;
    gap: 8px;
    align-items: stretch;

    .secret-value {
      flex: 1;
      font-family: 'Monaco', 'Consolas', monospace;
      font-size: 13px;
      background: var(--gray-100);
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      padding: 12px;
      word-break: break-all;
      color: var(--gray-900);
    }

    .copy-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
  }
}
</style>
