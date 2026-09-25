<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  Bot,
  Microscope,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Upload,
  Wrench
} from '@lucide/vue'

import { userApi } from '@/apis/user_api'
import { agentApi } from '@/apis/agent_api'
import { mcpApi } from '@/apis/mcp_api'
import AgentRuntimeConfigForm from '@/components/AgentRuntimeConfigForm.vue'
import AgentSelfSkillEntry from '@/components/AgentSelfSkillEntry.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import { MAX_IMAGE_UPLOAD_SIZE_BYTES, MAX_IMAGE_UPLOAD_SIZE_MB } from '@/utils/upload_limits'
import { normalizeAgent } from '@/utils/agentConfigUtils'
import { parseMcpManifest } from '@/utils/mcpManifest'
import { createAgentResources } from '@/utils/agentCreateResources'

const props = defineProps({
  backendOptions: { type: Array, default: () => [] }
})

const emit = defineEmits(['saved'])

const userStore = useUserStore()
const agentStore = useAgentStore()

const DEFAULT_AGENT_BACKEND_ID = 'ChatbotAgent'
const SUB_AGENT_BACKEND_ID = 'SubAgentBackend'
const runtimeAgentModalTabs = ['model', 'tools', 'other']

const showAgentModal = ref(false)
const editingAgentId = ref(null)
// openEdit 已用 detail.can_manage 把关；这里显式记录，避免把「能打开弹窗」误当成「能改专属技能」。
const canManageEditingAgent = ref(false)
const agentModalActiveTab = ref('basic')
const agentIconUploading = ref(false)
const saving = ref(false)
const agentShareConfigFormRef = ref(null)
const agentNameInputRef = ref(null)
const createSkillFile = ref(null)
const mcpManifestText = ref('')
const createProgress = reactive({
  createdMcpSlugs: [],
  createdMcpConfigs: {},
  agent: null,
  skillUploaded: false,
  uncertainStep: ''
})
const createError = ref('')
const createCompleted = ref(false)
const pendingCreateDraft = ref(null)
const hasPendingCreate = computed(() =>
  createProgress.createdMcpSlugs.length > 0 || Boolean(createProgress.agent) || Boolean(createProgress.uncertainStep)
)
const createAgentAlreadySaved = computed(() => !editingAgentId.value && Boolean(createProgress.agent))
const createActionLabel = computed(() => {
  if (createProgress.uncertainStep) return '结果待核对'
  if (createProgress.agent && !createSkillFile.value) return '完成创建'
  return hasPendingCreate.value ? '重试剩余步骤' : '创建'
})
const agentShareConfig = ref({
  version: 2,
  read_scope: { access_level: 'user', department_ids: [], user_uids: [] },
  manage_scope: null
})
const agentForm = reactive({
  slug: '',
  name: '',
  backend_id: DEFAULT_AGENT_BACKEND_ID,
  description: '',
  icon: ''
})

// 基本配置的原始基线，用于在标题栏显示「有修改」状态。slug / backend_id
// 仅在创建模式可编辑，因此新建时不参与比对。
const originalAgentForm = ref({ name: '', description: '', icon: '' })
const originalShareConfig = ref(null)

const snapshotAgentForm = () => ({
  name: (agentForm.name || '').trim(),
  description: (agentForm.description || '').trim(),
  icon: (agentForm.icon || '').trim()
})

const cloneShareConfig = (share) => {
  if (!share) return null
  const cloneScope = (scope) =>
    scope
      ? {
          access_level: scope.access_level,
          department_ids: [...(scope.department_ids || [])],
          user_uids: [...(scope.user_uids || [])]
        }
      : null
  return {
    version: share.version,
    read_scope: cloneScope(share.read_scope),
    manage_scope: cloneScope(share.manage_scope)
  }
}

const snapshotShareConfig = () => {
  if (!editingAgentId.value) return null
  if (isBuiltinAgent({ id: editingAgentId.value })) {
    return cloneShareConfig({
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    })
  }
  return cloneShareConfig(agentShareConfig.value)
}

const stringifyShareConfig = (share) => {
  if (!share) return ''
  const sortIds = (arr) => [...(arr || [])].map((v) => String(v)).sort()
  return JSON.stringify({
    version: share.version,
    read_scope: {
      access_level: share.read_scope?.access_level || null,
      department_ids: sortIds(share.read_scope?.department_ids),
      user_uids: sortIds(share.read_scope?.user_uids)
    },
    manage_scope: share.manage_scope
      ? {
          access_level: share.manage_scope.access_level,
          department_ids: sortIds(share.manage_scope.department_ids),
          user_uids: sortIds(share.manage_scope.user_uids)
        }
      : null
  })
}

const hasProfileChanges = computed(() => {
  if (!editingAgentId.value) return false
  const currentForm = snapshotAgentForm()
  const baselineForm = originalAgentForm.value
  if (
    currentForm.name !== baselineForm.name ||
    currentForm.description !== baselineForm.description ||
    currentForm.icon !== baselineForm.icon
  ) {
    return true
  }
  if (!canEditAgentShareConfig.value) return false
  const currentShare = snapshotShareConfig()
  const baselineShare = originalShareConfig.value
  if (!currentShare || !baselineShare) return false
  return stringifyShareConfig(currentShare) !== stringifyShareConfig(baselineShare)
})

const captureProfileBaseline = () => {
  originalAgentForm.value = snapshotAgentForm()
  originalShareConfig.value = snapshotShareConfig()
}

const hasAnyUnsavedChanges = computed(() => agentStore.hasConfigChanges || hasProfileChanges.value)

const agentModalMenuItems = computed(() => {
  const items = [{ key: 'basic', label: '基本信息', icon: Bot }]
  if (editingAgentId.value) {
    items.push(
      { key: 'model', label: '模型配置', icon: SlidersHorizontal },
      { key: 'tools', label: '工具配置', icon: Wrench },
      { key: 'self-skill', label: '专属技能', icon: Sparkles },
      { key: 'other', label: '其他配置', icon: Settings2 }
    )
  }
  return items
})

const showAgentModalSidebar = computed(() => agentModalMenuItems.value.length > 1)
const runtimeConfigSegment = computed(() =>
  runtimeAgentModalTabs.includes(agentModalActiveTab.value) ? agentModalActiveTab.value : 'model'
)
const isRuntimeAgentModalTab = (key) => runtimeAgentModalTabs.includes(key)
const getDefaultBackendId = () => DEFAULT_AGENT_BACKEND_ID
const isSubAgentBackend = (backendId) => backendId === SUB_AGENT_BACKEND_ID

const getInitialShareConfig = () => ({
  version: 2,
  read_scope: {
    access_level: 'user',
    department_ids: [],
    user_uids: userStore.uid ? [userStore.uid] : []
  },
  manage_scope: null
})

const normalizeShareConfigForPayload = () => {
  if (isBuiltinAgent({ id: editingAgentId.value })) {
    return {
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    }
  }
  return agentShareConfig.value || getInitialShareConfig()
}

const isEditingBuiltinAgent = computed(() => isBuiltinAgent({ id: editingAgentId.value }))
const canEditAgentShareConfig = computed(() => !isEditingBuiltinAgent.value)
const getAgentShareAllowedLevels = () => {
  if (isEditingBuiltinAgent.value) return ['global']
  if (userStore.isAdmin) return ['global', 'department', 'user']
  return ['user']
}

const agentModalTitle = computed(() => (editingAgentId.value ? '编辑智能体' : '新增智能体'))
const agentPreviewDefaultIcon = computed(() =>
  editingAgentId.value ? generatePixelAvatar(editingAgentId.value) : ''
)
const agentPreviewName = computed(() => agentForm.name || editingAgentId.value || '智能体')
const selectedBackendOption = computed(() =>
  props.backendOptions.find((backend) => backend.value === agentForm.backend_id)
)
const selectedBackendLabel = computed(
  () => selectedBackendOption.value?.label || agentForm.backend_id || '未选择'
)
const selectedBackendIcon = computed(() => {
  const backendText = `${agentForm.backend_id} ${selectedBackendLabel.value}`.toLowerCase()
  return backendText.includes('deep') || backendText.includes('search') ? Microscope : Bot
})

const generateDefaultAgentProfile = () => {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')
  return {
    name: '新建智能体',
    slug: `agent-${stamp}`
  }
}

const resetAgentForm = () => {
  const defaults = editingAgentId.value ? {} : generateDefaultAgentProfile()
  Object.assign(agentForm, {
    slug: '',
    name: '',
    backend_id: getDefaultBackendId(),
    description: '',
    icon: '',
    ...defaults
  })
  agentShareConfig.value = getInitialShareConfig()
}

const focusAgentNameInput = async () => {
  await nextTick()
  let el = agentNameInputRef.value
  if (!el) {
    // after-open-change 可能在 input 还没挂载时触发，这里兜底
    await new Promise((resolve) => setTimeout(resolve, 50))
    el = agentNameInputRef.value
  }
  if (!el) return
  el.focus?.()
  el.select?.()
}

const handleAgentModalAfterOpenChange = (open) => {
  if (open && !editingAgentId.value) focusAgentNameInput()
}

/** 清空创建草稿及其步骤记录。 */
const resetCreateResources = () => {
  createSkillFile.value = null
  mcpManifestText.value = ''
  Object.assign(createProgress, {
    createdMcpSlugs: [],
    createdMcpConfigs: {},
    agent: null,
    skillUploaded: false,
    uncertainStep: ''
  })
  createError.value = ''
  createCompleted.value = false
  pendingCreateDraft.value = null
}

/** 放弃当前草稿并准备新的创建表单。 */
const startNewCreate = () => {
  resetAgentForm()
  resetCreateResources()
}

/** 放弃部分创建结果前刷新列表，让已落库智能体可见。 */
const abandonCreateDraft = async () => {
  saving.value = true
  try {
    await agentStore.fetchAgents()
    emit('saved', { mode: 'discard' })
    startNewCreate()
  } catch (error) {
    message.error(error.message || '刷新智能体列表失败')
  } finally {
    saving.value = false
  }
}

const openCreate = () => {
  editingAgentId.value = null
  canManageEditingAgent.value = false
  agentModalActiveTab.value = 'basic'
  if (!hasPendingCreate.value || createCompleted.value) {
    startNewCreate()
  } else if (pendingCreateDraft.value) {
    Object.assign(agentForm, pendingCreateDraft.value.form)
    agentShareConfig.value = pendingCreateDraft.value.shareConfig
  }
  agentStore.resetAgentConfig()
  showAgentModal.value = true
  focusAgentNameInput()
}

const openEdit = async (agent) => {
  const agentId = typeof agent === 'string' ? agent : agent?.id
  if (!agentId) return

  const detail = await agentStore.fetchAgentDetail(agentId, true)
  if (!detail?.can_manage) {
    message.warning('当前智能体不可编辑')
    return
  }

  if (hasPendingCreate.value && !createCompleted.value) {
    pendingCreateDraft.value = {
      form: { ...agentForm },
      shareConfig: cloneShareConfig(agentShareConfig.value)
    }
  }
  editingAgentId.value = detail.id
  canManageEditingAgent.value = Boolean(detail?.can_manage)
  agentModalActiveTab.value = 'basic'
  Object.assign(agentForm, {
    slug: detail.id || detail.slug || '',
    name: detail.name || '',
    backend_id: detail.backend_id || DEFAULT_AGENT_BACKEND_ID,
    description: detail.description || '',
    icon: detail.icon || ''
  })
  agentShareConfig.value = isBuiltinAgent(detail)
    ? {
        version: 2,
        read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
        manage_scope: null
      }
    : detail.share_config || getInitialShareConfig()
  await agentStore.selectAgent(detail.id, { allowSubagent: true })
  captureProfileBaseline()
  showAgentModal.value = true
}

const restoreChatAgentSelectionIfNeeded = async () => {
  if (!agentStore.selectedAgent?.is_subagent) return
  const fallbackAgentId = (agentStore.agents || []).find((agent) => !agent.is_subagent)?.id
  if (fallbackAgentId) await agentStore.selectAgent(fallbackAgentId)
}

const closeAgentModal = async () => {
  if (saving.value || agentIconUploading.value) return
  showAgentModal.value = false
  await restoreChatAgentSelectionIfNeeded()
}

const beforeAgentIconUpload = (file) => {
  if (!file.type.startsWith('image/')) {
    message.error('只能上传图片文件')
    return false
  }

  if (file.size > MAX_IMAGE_UPLOAD_SIZE_BYTES) {
    message.error(`图片大小不能超过 ${MAX_IMAGE_UPLOAD_SIZE_MB}MB`)
    return false
  }

  uploadAgentIcon(file)
  return false
}

const uploadAgentIcon = async (file) => {
  agentIconUploading.value = true
  try {
    const data = await userApi.uploadImage(file)
    agentForm.icon = data.image_url || data.url || ''
    message.success('图标上传成功')
  } catch (error) {
    message.error(error.message || '图标上传失败')
  } finally {
    agentIconUploading.value = false
  }
}

const buildAgentPayload = () => {
  const payload = {
    name: agentForm.name.trim(),
    description: agentForm.description.trim() || null,
    icon: agentForm.icon.trim() || null,
    share_config: normalizeShareConfigForPayload(),
    is_subagent: isSubAgentBackend(agentForm.backend_id)
  }

  if (!editingAgentId.value) {
    payload.slug = agentForm.slug.trim() || undefined
    payload.backend_id = agentForm.backend_id
  }

  return payload
}

/** 在提交前保存待上传的专属技能 ZIP。 */
const beforeCreateSkillUpload = (file) => {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error('请选择 ZIP 格式的 Skill')
    return false
  }
  createSkillFile.value = file
  createProgress.skillUploaded = false
  createError.value = ''
  return false
}

/** 取消可选技能上传，保留已经创建的智能体。 */
const removeCreateSkill = () => {
  createSkillFile.value = null
  createError.value = ''
}

/** 将表单值交给资源创建用例并保留步骤进度。 */
const createAgentWithResources = async (payload) => {
  const servers = userStore.isAdmin ? parseMcpManifest(mcpManifestText.value) : []
  return createAgentResources({
    payload,
    servers,
    skillFile: createSkillFile.value,
    progress: createProgress,
    api: {
      listMcps: mcpApi.getMcpServers,
      createMcp: mcpApi.createMcpServer,
      createAgent: async (data) => normalizeAgent((await agentApi.createAgent(data)).agent),
      uploadSkill: agentApi.uploadAgentSelfSkill
    }
  })
}

const saveAgent = async () => {
  if (!agentForm.name.trim()) {
    agentModalActiveTab.value = 'basic'
    message.error('请填写智能体名称')
    return
  }

  const validation = canEditAgentShareConfig.value
    ? agentShareConfigFormRef.value?.validate?.()
    : null
  if (validation && !validation.valid) {
    agentModalActiveTab.value = 'basic'
    message.error(validation.message)
    return
  }

  saving.value = true
  try {
    const payload = buildAgentPayload()
    if (editingAgentId.value) {
      if (agentStore.hasConfigChanges) {
        payload.config_json = { context: agentStore.changedAgentConfig }
      }
      const updated = await agentStore.updateAgentProfile(editingAgentId.value, payload)
      captureProfileBaseline()
      emit('saved', { mode: 'edit', agent: updated })
      message.success('智能体已保存')
    } else {
      const created = await createAgentWithResources(payload)
      await agentStore.fetchAgents()
      if (!created.is_subagent) {
        try {
          await agentStore.selectAgent(created.id)
        } catch {
          message.warning('智能体已创建，但自动切换失败，请从列表中选择')
        }
      }
      emit('saved', { mode: 'create', agent: normalizeAgent(created) })
      message.success('智能体已创建')
      createCompleted.value = true
    }
    showAgentModal.value = false
    await restoreChatAgentSelectionIfNeeded()
  } catch (error) {
    if (editingAgentId.value) {
      message.error(error.message || '保存智能体失败')
    } else {
      const progress = [
        ...(createProgress.createdMcpSlugs.length ? [`已创建 MCP：${createProgress.createdMcpSlugs.join('、')}`] : []),
        ...(createProgress.agent ? [`已创建智能体：${createProgress.agent.id}`] : [])
      ]
      const nextStep = createProgress.uncertainStep
        ? `${createProgress.uncertainStep}请求结果未确认，已停止自动重试。请在管理列表核对后手动处理`
        : progress.length ? '后续步骤失败，可重试剩余步骤；关闭后再次新增也会保留进度' : ''
      createError.value = [progress.join('；'), nextStep, error.message || '保存智能体失败']
        .filter(Boolean)
        .join('。')
      message.error(createError.value)
    }
  } finally {
    saving.value = false
  }
}

defineExpose({
  openCreate,
  openEdit,
  close: closeAgentModal
})
</script>

<template>
  <a-modal
    v-model:open="showAgentModal"
    class="agent-edit-modal"
    :width="editingAgentId ? 820 : 740"
    :footer="null"
    :closable="false"
    @cancel="closeAgentModal"
    @after-open-change="handleAgentModalAfterOpenChange"
  >
    <template #title>
      <div class="agent-modal-titlebar">
        <span class="agent-modal-title">{{ agentModalTitle }}</span>
        <div class="agent-modal-actions" v-if="hasAnyUnsavedChanges || !editingAgentId">
          <a-button size="small" :disabled="saving" @click="closeAgentModal">取消</a-button>
          <a-button size="small" type="primary" :loading="saving" :disabled="!editingAgentId && Boolean(createProgress.uncertainStep)" @click="saveAgent">
            {{ editingAgentId ? '保存（有修改）' : createActionLabel }}
          </a-button>
        </div>
      </div>
    </template>
    <div
      class="agent-modal-content"
      :class="{
        'without-sidebar': !showAgentModalSidebar,
        'create-mode': !editingAgentId
      }"
    >
      <aside v-if="showAgentModalSidebar" class="agent-modal-sidebar" aria-label="智能体配置分组">
        <button
          v-for="item in agentModalMenuItems"
          :key="item.key"
          type="button"
          class="agent-modal-nav-item"
          :class="{ active: agentModalActiveTab === item.key }"
          @click="agentModalActiveTab = item.key"
        >
          <span class="nav-item-main">
            <component :is="item.icon" :size="16" />
            <span>{{ item.label }}</span>
          </span>
          <span v-if="item.key === 'model' && agentStore.hasConfigChanges" class="nav-dirty-dot" />
        </button>
      </aside>

      <div class="agent-modal-main">
        <section v-show="agentModalActiveTab === 'basic'" class="agent-modal-section">
          <div class="agent-profile-header">
            <div class="agent-icon-preview" aria-label="智能体图标、名称与后端">
              <div class="agent-profile-main">
                <a-upload
                  :show-upload-list="false"
                  :before-upload="beforeAgentIconUpload"
                  :disabled="agentIconUploading || saving || createAgentAlreadySaved"
                  accept="image/*"
                >
                  <div
                    class="agent-icon-upload"
                    :class="{
                      uploading: agentIconUploading,
                      'is-empty': !agentForm.icon && !editingAgentId
                    }"
                  >
                    <FallbackAvatar
                      v-if="agentForm.icon || editingAgentId"
                      :src="agentForm.icon"
                      :default-src="agentPreviewDefaultIcon"
                      :name="agentPreviewName"
                      :seed="editingAgentId || agentForm.slug || agentForm.name"
                      kind="agent"
                      :size="56"
                      shape="rounded"
                      :alt="`${agentForm.name || '智能体'}图标`"
                      class="agent-icon-preview-avatar"
                    />
                    <div class="agent-icon-mask">
                      <RefreshCw v-if="agentIconUploading" :size="16" class="spinning" />
                      <Upload v-else :size="16" />
                      <span>{{ agentForm.icon ? '更换图标' : '上传图标' }}</span>
                    </div>
                  </div>
                </a-upload>
                <div class="agent-icon-preview-text">
                  <input
                    ref="agentNameInputRef"
                    v-model="agentForm.name"
                    :disabled="saving || createAgentAlreadySaved"
                    class="agent-inline-name-input"
                    type="text"
                    placeholder="点击输入智能体名称"
                    aria-label="智能体名称"
                  />
                  <input
                    v-if="!editingAgentId"
                    v-model="agentForm.slug"
                    :disabled="saving || createAgentAlreadySaved"
                    class="agent-inline-slug-input"
                    type="text"
                    placeholder="标识可选，留空自动生成"
                    aria-label="智能体标识"
                  />
                  <span v-else class="agent-inline-slug">{{
                    agentForm.slug || editingAgentId
                  }}</span>
                </div>
              </div>
              <div
                class="agent-backend-summary"
                :class="{ editable: !editingAgentId }"
                aria-label="智能体后端"
              >
                <span class="agent-backend-icon">
                  <component :is="selectedBackendIcon" :size="16" />
                </span>
                <div class="agent-backend-text">
                  <span class="agent-backend-label">智能体后端</span>
                  <a-select
                    v-if="!editingAgentId"
                    v-model:value="agentForm.backend_id"
                    :disabled="saving || createAgentAlreadySaved"
                    class="agent-backend-select"
                    :bordered="false"
                    :options="backendOptions"
                  />
                  <span v-else class="agent-backend-name">{{ selectedBackendLabel }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="modal-form">
            <label class="form-label full-width">
              <span>描述</span>
              <a-textarea
                v-model:value="agentForm.description"
                :disabled="saving || createAgentAlreadySaved"
                class="agent-description-textarea"
                :rows="3"
                placeholder="可选"
              />
            </label>
          </div>

          <div v-if="!editingAgentId" class="create-resources">
            <div class="section-heading">创建时配置能力</div>
            <a-alert v-if="createError" type="warning" show-icon :message="createError" />
            <a-button v-if="hasPendingCreate" type="link" size="small" class="create-reset" :disabled="saving" @click="abandonCreateDraft">
              放弃当前草稿并新建（已创建资源会保留）
            </a-button>
            <label class="form-label full-width">
              <span>专属技能 ZIP（可选）</span>
              <a-upload :show-upload-list="false" :before-upload="beforeCreateSkillUpload" accept=".zip" :disabled="saving || Boolean(createProgress.uncertainStep)">
                <span class="skill-upload-trigger"><Upload :size="14" /> 选择 ZIP</span>
              </a-upload>
              <span v-if="createSkillFile" class="resource-hint">{{ createSkillFile.name }}</span>
              <a-button v-if="createSkillFile" type="link" size="small" :disabled="saving || Boolean(createProgress.uncertainStep)" @click="removeCreateSkill">移除</a-button>
            </label>
            <label v-if="userStore.isAdmin" class="form-label full-width">
              <span>MCP 清单（可选）</span>
              <a-textarea v-model:value="mcpManifestText" :rows="5" :disabled="saving || Boolean(createProgress.agent) || Boolean(createProgress.uncertainStep)" placeholder='{"mcpServers":{"search":{"type":"http","url":"https://example.com/mcp","extra_data":{"name":"搜索"}}}}' />
              <span class="resource-hint">支持远程 HTTP / SSE 服务。清单中的服务会创建为系统 MCP，并绑定到此智能体。</span>
              <span v-if="createProgress.createdMcpSlugs.length && !createProgress.agent" class="resource-hint">已创建的 MCP 条目不能修改；可以修正尚未创建的条目后重试。</span>
            </label>
          </div>

          <div v-if="canEditAgentShareConfig && !createAgentAlreadySaved" class="share-config-block">
            <div class="section-heading">
              <span>共享权限</span>
            </div>
            <ShareConfigForm
              ref="agentShareConfigFormRef"
              v-model="agentShareConfig"
              :auto-select-user-dept="true"
              :allowed-access-levels="getAgentShareAllowedLevels()"
            />
          </div>
        </section>

        <section
          v-if="editingAgentId"
          v-show="isRuntimeAgentModalTab(agentModalActiveTab)"
          class="agent-modal-section runtime-section"
        >
          <AgentRuntimeConfigForm :segment="runtimeConfigSegment" :show-segmented="false" />
        </section>

        <section
          v-if="editingAgentId"
          v-show="agentModalActiveTab === 'self-skill'"
          class="agent-modal-section self-skill-section"
        >
          <AgentSelfSkillEntry :agent-id="editingAgentId" :can-manage="canManageEditingAgent" />
        </section>
      </div>
    </div>
  </a-modal>
</template>

<style lang="less" scoped>
.agent-modal-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.agent-modal-title {
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 600;
}

.agent-modal-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  :deep(.ant-btn) {
    min-width: 56px;
    border-radius: 6px;
    font-weight: 500;
  }

  :deep(.ant-btn-primary) {
    border-color: var(--main-700);
    background: var(--main-700);

    &:hover,
    &:focus {
      border-color: var(--main-800);
      background: var(--main-800);
    }
  }
}

.agent-modal-content {
  display: grid;
  grid-template-columns: 144px minmax(0, 1fr);
  height: min(72vh, 640px);
  min-height: 0;
  overflow: hidden;
  background: var(--gray-0);

  &.without-sidebar {
    grid-template-columns: minmax(0, 1fr);
  }

  &.create-mode {
    height: min(72vh, 640px);
    min-height: 0;
  }
}

.agent-modal-sidebar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  padding: 14px 10px;
  overflow-y: auto;
  border-right: 1px solid var(--gray-150);
  background: transparent;
}

.agent-modal-nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition:
    background 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease;

  &:hover {
    background: var(--gray-50);
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: 2px solid var(--main-100);
    outline-offset: 1px;
    border-color: var(--main-200);
  }

  &.active {
    background: var(--gray-100);
    color: var(--gray-900);

    span {
      font-weight: 600;
    }
  }
}

.nav-item-main {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 8px;

  svg {
    flex-shrink: 0;
    color: var(--gray-600);
  }
}

.agent-modal-nav-item.active .nav-item-main svg {
  color: var(--gray-700);
}

.nav-dirty-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-warning-600);
}

.agent-modal-main {
  min-width: 0;
  min-height: 0;
  overflow: hidden auto;
  overscroll-behavior: contain;
  padding: 22px 18px 24px 24px;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: var(--gray-300) transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    border: 2px solid transparent;
    border-radius: 999px;
    background: var(--gray-300);
    background-clip: content-box;
  }

  &::-webkit-scrollbar-thumb:hover {
    background: var(--gray-400);
    background-clip: content-box;
  }
}

.agent-modal-section {
  min-height: 0;
  background: var(--gray-0);
}

.runtime-section {
  display: flex;
  flex-direction: column;
  min-height: 100%;

  :deep(.agent-runtime-config-form) {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
    background: transparent;
  }

  :deep(.runtime-config-content) {
    flex: 1;
    min-width: 0;
    min-height: 0;
    padding: 0;
    overflow: visible;
  }
}

.self-skill-section {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 4px 0;

  :deep(.agent-self-skill-entry) {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
  }
}

.section-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

.agent-profile-header {
  margin-bottom: 16px;
}

.agent-icon-preview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 0;
  gap: 16px;

  :deep(.ant-upload) {
    display: block;
  }
}

.agent-profile-main {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.agent-icon-upload {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  background: var(--main-30);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    box-shadow 0.16s ease;

  .agent-icon-preview-avatar {
    width: 100%;
    height: 100%;
    border: 0;
  }

  &:hover,
  &:focus-within,
  &.uploading {
    border-color: var(--main-300);
    box-shadow: 0 0 0 3px var(--main-50);
  }

  &:hover .agent-icon-mask,
  &:focus-within .agent-icon-mask,
  &.uploading .agent-icon-mask,
  &.is-empty .agent-icon-mask {
    opacity: 1;
  }

  &.is-empty {
    border-style: dashed;
    background: var(--gray-0);
  }
}

.agent-icon-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: color-mix(in srgb, var(--gray-900) 62%, transparent);
  color: var(--gray-0);
  font-size: 11px;
  font-weight: 600;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.agent-icon-upload.is-empty .agent-icon-mask {
  background: transparent;
  color: var(--gray-600);
}

.agent-icon-preview-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
  line-height: 1.25;
}

.agent-inline-name-input {
  width: 200px;
  max-width: 100%;
  padding: 1px 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-900);
  caret-color: var(--main-700);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-0);
  }

  &:focus {
    border-color: var(--main-300);
    background: var(--gray-0);
    box-shadow: 0 0 0 3px var(--main-50);
    outline: none;
  }
}

.agent-inline-slug,
.agent-inline-slug-input {
  padding: 1px 4px;
  width: 200px;
  max-width: 100%;
  overflow: hidden;
  color: var(--gray-500);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-inline-slug-input {
  border: 1px solid transparent;
  border-radius: 2px;
  background: transparent;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover,
  &:focus {
    border-color: var(--gray-300);
    background: var(--gray-0);
    outline: none;
  }
}

.agent-backend-summary {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 10px;
  width: 190px;
  min-height: 56px;
  padding: 10px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  background: var(--gray-10);
  color: var(--gray-700);

  &.editable {
    padding-right: 8px;
  }
}

.agent-backend-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--gray-100);
  color: var(--gray-700);
}

.agent-backend-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 3px;
  line-height: 1.2;
}

.agent-backend-label {
  color: var(--gray-500);
  font-size: 11px;
}

.agent-backend-name {
  max-width: 128px;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-backend-select {
  width: 128px;
  margin: -3px 0 -5px -11px;

  :deep(.ant-select-selector) {
    background: transparent !important;
    box-shadow: none !important;
  }

  :deep(.ant-select-selection-item) {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
  }

  :deep(.ant-select-arrow) {
    color: var(--gray-500);
  }
}

.share-config-block {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--gray-150);
}

.create-resources {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--gray-150);
}

.create-resources .section-heading {
  margin-bottom: 0;
}

.create-reset {
  align-self: flex-start;
  height: auto;
  padding: 0;
  white-space: normal;
  text-align: left;
}

.create-resources .resource-hint {
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 400;
}

.skill-upload-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 4px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--gray-800);
  font-size: 12px;
  cursor: pointer;
}

.skill-upload-trigger:hover {
  border-color: var(--main-500);
  color: var(--main-700);
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-label {
  display: flex;
  flex-direction: column;
  gap: 6px;

  > span {
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
  }
}

.agent-description-textarea {
  min-height: 80px;
  padding: 10px 12px;
  border-color: var(--gray-200);
  border-radius: 8px;
  background: var(--gray-10);
  color: var(--gray-900);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-0);
  }

  &:focus {
    border-color: var(--main-300);
    background: var(--gray-0);
    box-shadow: 0 0 0 3px var(--main-50);
  }
}

.full-width {
  grid-column: 1 / -1;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 768px) {
  .agent-modal-content {
    grid-template-columns: 1fr;
    height: min(78vh, 680px);
  }

  .agent-modal-sidebar {
    flex-direction: row;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
  }

  .agent-icon-preview {
    flex-wrap: wrap;
  }

  .agent-profile-main,
  .agent-backend-summary {
    width: 100%;
  }

  .agent-icon-preview-text {
    flex: 1;
  }
}

:global(.agent-edit-modal .ant-modal-content) {
  overflow: hidden;
  padding: 0;
  border-radius: 12px;
}

:global(.agent-edit-modal .ant-modal-header) {
  margin: 0;
  padding: 10px 24px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
}

:global(.agent-edit-modal .ant-modal-title) {
  width: 100%;
}

:global(.agent-edit-modal .ant-modal-body) {
  padding: 0;
}
</style>
