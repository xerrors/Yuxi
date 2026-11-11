<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  FileArchive,
  Microscope,
  Plus,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Upload,
  Wrench,
  X
} from '@lucide/vue'

import { userApi } from '@/apis/user_api'
import { agentApi } from '@/apis/agent_api'
import { skillApi } from '@/apis/skill_api'
import { isAllAgentResourceSelection, normalizeAgentConfigurableItems } from '@/modules/agents/model/agentConfigUtils'
import McpFormModal from '@/modules/extensions/ui/McpFormModal.vue'
import SkillInstallFlowModal from '@/modules/extensions/ui/SkillInstallFlowModal.vue'
import AgentBoundSkillPanel from '@/modules/agents/ui/AgentBoundSkillPanel.vue'
import AgentRuntimeConfigForm from '@/modules/agents/ui/AgentRuntimeConfigForm.vue'
import ShareConfigForm from '@/modules/agents/ui/ShareConfigForm.vue'
import { cloneShareConfig } from '@/modules/agents/model/shareConfig'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import CollapseTransition from '@/shared/ui/CollapseTransition.vue'
import { isBuiltinAgent, useAgentStore } from '@/modules/agents/model/agent'
import { useUserStore } from '@/modules/identity'
import { MAX_IMAGE_UPLOAD_SIZE_BYTES, MAX_IMAGE_UPLOAD_SIZE_MB } from '@/shared/lib/upload_limits'

const props = defineProps({
  backendOptions: { type: Array, default: () => [] }
})

const emit = defineEmits(['saved'])

const userStore = useUserStore()
const agentStore = useAgentStore()
const router = useRouter()

const DEFAULT_AGENT_BACKEND_ID = 'ChatbotAgent'
const runtimeAgentModalTabs = ['model', 'tools', 'other']

const showAgentModal = ref(false)
const editingAgentId = ref(null)
const editingCapabilities = ref({})
const agentModalActiveTab = ref('basic')
const agentIconUploading = ref(false)
const saving = ref(false)
const boundSkillSaving = ref(false)
const createSkillFile = ref(null)
const createContext = ref({})
const createConfigItems = ref({})
const createConfigLoading = ref(false)
const createConfigError = ref('')
const createAdvancedOpen = ref(false)
const shareConfigHeadingRef = ref(null)
const shareConfigOpen = ref(false)
const createConfigSectionRef = ref(null)
const createError = ref('')
const createOutcomeUnknown = ref(false)
const runtimeFormRef = ref(null)
const mcpCreateOpen = ref(false)
const sharedSkillPreparing = ref(false)
const sharedSkillOpen = ref(false)
const sharedSkillFlow = ref(null)
const resourceCreationOpen = computed(() => mcpCreateOpen.value || sharedSkillPreparing.value || sharedSkillOpen.value)
const openingBlocked = computed(() => saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value
  || (showAgentModal.value && createOutcomeUnknown.value))
onBeforeRouteLeave(() => !(saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value))
let createConfigRevision = 0
// 新的打开或关闭操作使旧编辑响应失效，避免覆盖创建草稿。
let modalOpenRevision = 0
const agentShareConfigFormRef = ref(null)
const shareConfigNeedsRepair = ref(false)
const agentNameInputRef = ref(null)
const agentShareConfig = ref({
  version: 2,
  read_scope: { access_level: 'user', department_ids: [], user_uids: [] },
  manage_scope: null
})
const agentForm = reactive({
  slug: '',
  name: '',
  backend_id: DEFAULT_AGENT_BACKEND_ID,
  visibility: 'private',
  description: '',
  icon: ''
})

// 基本配置基线用于判断是否有待保存的改动。slug / backend_id
// 仅在创建模式可编辑，因此新建时不参与比对。
const runtimeConfig = computed({
  get: () => (editingAgentId.value ? agentStore.agentConfig : createContext.value),
  set: (value) => {
    if (editingAgentId.value) agentStore.updateAgentConfig(value)
    else createContext.value = value
  }
})
const runtimeConfigItems = computed(() => editingAgentId.value
  ? agentStore.configurableItems : createConfigItems.value)
const runtimeConfigReadonly = computed(() => saving.value || boundSkillSaving.value || resourceCreationOpen.value
  || createOutcomeUnknown.value || createConfigLoading.value
  || Boolean(editingAgentId.value && !agentStore.selectedAgent?.can_manage))
const creatableResourceKinds = computed(() => [
  ...(userStore.isSuperAdmin ? ['mcps'] : []),
  ...(userStore.isAdmin ? ['skills'] : [])
])

const originalAgentForm = ref({ name: '', description: '', icon: '', visibility: 'private' })
const originalShareConfig = ref(null)

const snapshotAgentForm = () => ({
  name: (agentForm.name || '').trim(),
  description: (agentForm.description || '').trim(),
  icon: (agentForm.icon || '').trim(),
  visibility: agentForm.visibility
})

const snapshotShareConfig = () => {
  if (!editingAgentId.value || !isAgentShared.value) return null
  if (isBuiltinAgent({ agent_id: editingAgentId.value })) {
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
  if (shareConfigNeedsRepair.value) return true
  const currentForm = snapshotAgentForm()
  const baselineForm = originalAgentForm.value
  if (
    currentForm.name !== baselineForm.name ||
    currentForm.description !== baselineForm.description ||
    currentForm.icon !== baselineForm.icon ||
    currentForm.visibility !== baselineForm.visibility
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
      { key: 'skill', label: '专属 Skill', icon: Microscope },
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
const getDefaultBackendId = () =>
  props.backendOptions.length === 1 ? props.backendOptions[0].value : DEFAULT_AGENT_BACKEND_ID

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
  if (isBuiltinAgent({ agent_id: editingAgentId.value })) {
    return {
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    }
  }
  return agentShareConfig.value || getInitialShareConfig()
}

const isEditingBuiltinAgent = computed(() => isBuiltinAgent({ agent_id: editingAgentId.value }))
const canEditAgentShareConfig = computed(() =>
  editingCapabilities.value.can_share ||
  (!editingAgentId.value && userStore.isAdmin)
)
const isAgentShared = computed(() => agentForm.visibility === 'shared')
const agentSharingLabel = computed(() => {
  if (!isAgentShared.value) return '私有'
  return agentShareConfig.value?.read_scope?.access_level === 'global' ? '全局共享' : '共享'
})
const canToggleAgentSharing = computed(
  () => !editingAgentId.value || editingCapabilities.value.visibility === 'private'
)
const getAgentShareAllowedLevels = () => {
  if (isEditingBuiltinAgent.value) return ['global']
  if (userStore.isAdmin) return ['global', 'department', 'user']
  return ['user']
}

const agentModalTitle = computed(() => (editingAgentId.value ? '编辑智能体' : '新增智能体'))
const agentPreviewName = computed(() => agentForm.name || editingAgentId.value || '智能体')
const generateDefaultAgentProfile = () => {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')
  return {
    name: '新建智能体',
    slug: `agent-${stamp}`
  }
}

const resetAgentForm = () => {
  shareConfigOpen.value = false
  shareConfigNeedsRepair.value = false
  const defaults = editingAgentId.value ? {} : generateDefaultAgentProfile()
  Object.assign(agentForm, {
    slug: '',
    name: '',
    backend_id: getDefaultBackendId(),
    visibility: 'private',
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

const openCreate = () => {
  if (openingBlocked.value) return
  modalOpenRevision += 1
  editingAgentId.value = null
  editingCapabilities.value = {}
  agentModalActiveTab.value = 'basic'
  resetAgentForm()
  createSkillFile.value = null
  createContext.value = {}
  createConfigItems.value = {}
  createConfigError.value = ''
  createConfigLoading.value = false
  createAdvancedOpen.value = false
  createError.value = ''
  createConfigRevision += 1
  createOutcomeUnknown.value = false
  agentStore.resetAgentConfig()
  showAgentModal.value = true
  focusAgentNameInput()
}

/** 刷新 Schema 只改变候选项，不覆盖创建草稿。 */
const loadCreateConfig = async () => {
  const revision = ++createConfigRevision
  const modalRevision = modalOpenRevision
  const backendId = agentForm.backend_id
  createConfigLoading.value = true
  createConfigError.value = ''
  try {
    const info = await agentApi.getAgentBackendDetail(backendId)
    if (revision !== createConfigRevision || modalRevision !== modalOpenRevision) return
    createConfigItems.value = normalizeAgentConfigurableItems(info.configurable_items)
  } catch (error) {
    if (revision !== createConfigRevision || modalRevision !== modalOpenRevision) return
    createConfigError.value = error.message || '配置项加载失败'
  } finally {
    if (revision === createConfigRevision && modalRevision === modalOpenRevision) createConfigLoading.value = false
  }
}
const toggleCreateAdvanced = async () => {
  if (saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value || createOutcomeUnknown.value) return
  createAdvancedOpen.value = !createAdvancedOpen.value
  await nextTick()
  if (createAdvancedOpen.value) createConfigSectionRef.value?.focus()
  else focusAgentNameInput()
  if (createAdvancedOpen.value && Object.keys(createConfigItems.value).length === 0) await loadCreateConfig()
}
watch(() => agentForm.backend_id, () => {
  if (editingAgentId.value || !showAgentModal.value) return
  if (Object.keys(createContext.value).length) message.info('后端已切换，请重新选择高级配置')
  createContext.value = {}
  createConfigItems.value = {}
  createConfigError.value = ''
  createConfigLoading.value = false
  createConfigRevision += 1
  if (createAdvancedOpen.value) loadCreateConfig()
})

const refreshRuntimeOptions = async () => {
  if (!editingAgentId.value) return loadCreateConfig()
  try {
    await agentStore.fetchAgentDetail(editingAgentId.value, true)
    message.success('配置选项已刷新')
  } catch (error) {
    message.error(error.message || '配置选项刷新失败')
  }
}
/** 共享资源独立发布，只将成功标识加入 Context。 */
const selectCreatedResources = (field, slugs) => {
  if (!slugs.length || isAllAgentResourceSelection(runtimeConfig.value[field], runtimeConfigItems.value[field])) return
  const selected = runtimeConfig.value[field] ?? runtimeConfigItems.value[field]?.default
  runtimeConfig.value = {
    ...runtimeConfig.value,
    [field]: [...new Set([...(Array.isArray(selected) ? selected : []), ...slugs])]
  }
}
const openMcpCreate = () => {
  if (!userStore.isSuperAdmin || runtimeConfigReadonly.value) return
  runtimeFormRef.value?.closeSelectionModal()
  mcpCreateOpen.value = true
}
const handleMcpCreated = async (server) => {
  if (server?.slug) selectCreatedResources('mcps', [server.slug])
  await refreshRuntimeOptions()
}
const beforeSharedSkillUpload = async (file) => {
  if (!userStore.isAdmin || runtimeConfigReadonly.value) return false
  if (!file.name.toLowerCase().endsWith('.zip') || file.size > 10 * 1024 * 1024) {
    message.error('请上传不超过 10 MiB 的 Skill ZIP 文件')
    return false
  }
  runtimeFormRef.value?.closeSelectionModal()
  sharedSkillPreparing.value = true
  try {
    const result = await skillApi.prepareSkillUpload(file)
    sharedSkillFlow.value = { kind: 'draft', title: '创建共享 Skill', drafts: [result.data] }
    sharedSkillOpen.value = true
  } catch (error) {
    message.error(error.message || 'Skill 解析失败')
  } finally {
    sharedSkillPreparing.value = false
  }
  return false
}
const handleSharedSkillCreated = async ({ slugs = [] }) => {
  selectCreatedResources('skills', slugs)
  await refreshRuntimeOptions()
}
const manageRuntimeResource = async (kind) => {
  if (!editingAgentId.value) {
    message.warning('请先创建智能体；新增连接器或共享 Skill 可使用旁边的创建按钮')
    return
  }
  const tabs = { knowledges: 'knowledge', tools: 'tools', mcps: 'mcp', skills: 'skills' }
  await navigateToSkill({ path: '/extensions', query: { tab: tabs[kind] } })
}

/** 文件只留在创建草稿中，随一次创建请求上传。 */
const beforeCreateSkillUpload = (file) => {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error('请上传 Skill ZIP 文件')
  } else if (file.size > 10 * 1024 * 1024) {
    message.error('ZIP 文件不能超过 10 MiB')
  } else {
    createSkillFile.value = file
  }
  return false
}

const openEdit = async (agent) => {
  if (openingBlocked.value) return
  const agentId = typeof agent === 'string' ? agent : agent?.agent_id
  if (!agentId) return
  const revision = ++modalOpenRevision

  const detail = await agentStore.fetchAgentDetail(agentId, true)
  if (revision !== modalOpenRevision || openingBlocked.value) return
  if (!detail?.can_manage) {
    message.warning('当前智能体不可编辑')
    return
  }

  editingCapabilities.value = detail
  createOutcomeUnknown.value = false
  editingAgentId.value = detail.agent_id
  agentModalActiveTab.value = 'basic'
  Object.assign(agentForm, {
    slug: detail.agent_id || '',
    name: detail.name || '',
    backend_id: detail.backend_id || DEFAULT_AGENT_BACKEND_ID,
    visibility: detail.visibility,
    description: detail.description || '',
    icon: detail.icon || ''
  })
  shareConfigNeedsRepair.value = Boolean(detail.share_config_invalid)
  agentShareConfig.value = isBuiltinAgent(detail)
    ? {
        version: 2,
        read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
        manage_scope: null
      }
    : detail.visibility === 'private'
      ? getInitialShareConfig()
      : cloneShareConfig(detail.share_config, shareConfigNeedsRepair.value) ||
        getInitialShareConfig()
  await agentStore.selectAgent(detail.agent_id)
  if (revision !== modalOpenRevision || openingBlocked.value) return
  captureProfileBaseline()
  showAgentModal.value = true
}

const restoreChatAgentSelectionIfNeeded = async () => {
  if (agentStore.selectedAgent?.can_run) return
  const fallbackAgentId = (agentStore.agents || []).find((agent) => agent.can_run)?.agent_id
  if (fallbackAgentId) await agentStore.selectAgent(fallbackAgentId)
}

const selectAgentModalTab = (tab) => {
  if (boundSkillSaving.value) return
  agentModalActiveTab.value = tab
}

const closeAgentModal = async () => {
  if (saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value) return
  modalOpenRevision += 1
  showAgentModal.value = false
  createConfigRevision += 1
  createConfigLoading.value = false
  await restoreChatAgentSelectionIfNeeded()
}

/** 保留未保存的 Agent 配置，由父级统一执行 Skill 编辑跳转。 */
const navigateToSkill = async (route) => {
  if (saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value) return
  if (hasAnyUnsavedChanges.value) {
    message.warning('请先保存智能体配置，再编辑专属 Skill')
    return
  }
  const failure = await router.push(route)
  if (!failure) await closeAgentModal()
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
    ...(canEditAgentShareConfig.value && isAgentShared.value ? { share_config: normalizeShareConfigForPayload() } : {})
  }

  if (!editingAgentId.value) {
    payload.slug = agentForm.slug.trim() || undefined
    payload.backend_id = agentForm.backend_id
    payload.visibility = isAgentShared.value ? 'shared' : 'private'
    payload.config_json = { context: { ...createContext.value } }
  } else if (canEditAgentShareConfig.value && canToggleAgentSharing.value && isAgentShared.value) {
    payload.visibility = 'shared'
  }

  return payload
}

const saveAgent = async () => {
  if (saving.value || boundSkillSaving.value || agentIconUploading.value || resourceCreationOpen.value || createOutcomeUnknown.value || createConfigLoading.value) return
  if (!agentForm.name.trim()) {
    agentModalActiveTab.value = 'basic'
    if (!editingAgentId.value) {
      createAdvancedOpen.value = false
      createError.value = '请填写智能体名称'
    }
    message.error('请填写智能体名称')
    await focusAgentNameInput()
    return
  }

  const validation = canEditAgentShareConfig.value && isAgentShared.value
    ? agentShareConfigFormRef.value?.validate?.()
    : null
  if (validation && !validation.valid) {
    agentModalActiveTab.value = 'basic'
    if (!editingAgentId.value) {
      createAdvancedOpen.value = false
      createError.value = validation.message
      shareConfigOpen.value = true
    }
    message.error(validation.message)
    await nextTick()
    shareConfigHeadingRef.value?.focus()
    return
  }

  saving.value = true
  createError.value = ''
  let creatingRequest = false
  try {
    const payload = buildAgentPayload()
    if (editingAgentId.value) {
      if (agentStore.hasConfigChanges) {
        payload.config_json = { context: agentStore.changedAgentConfig }
      }
      const updated = await agentStore.updateAgentProfile(editingAgentId.value, payload)
      shareConfigNeedsRepair.value = false
      captureProfileBaseline()
      emit('saved', { mode: 'edit', agent: updated })
      message.success('智能体已保存')
    } else {
      creatingRequest = true
      const created = await agentStore.createAgent(payload, createSkillFile.value)
      emit('saved', { mode: 'create', agent: created })
      message.success('智能体已创建')
    }
    showAgentModal.value = false
    await restoreChatAgentSelectionIfNeeded()
  } catch (error) {
    if (creatingRequest && (!error.status || error.status >= 500)) {
      createOutcomeUnknown.value = true
      message.warning('创建结果尚未确认，请取消并刷新智能体列表核对')
    } else {
      if (creatingRequest) createError.value = error.message || '创建失败，请检查配置后重试'
      message.error(error.message || '保存智能体失败')
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
    :open="showAgentModal"
    class="agent-edit-modal"
    :class="{ 'create-agent-modal': !editingAgentId }"
    :width="editingAgentId ? 820 : 740"
    :centered="!editingAgentId"
    :footer="null"
    :closable="false"
    @cancel="closeAgentModal"
    @after-open-change="handleAgentModalAfterOpenChange"
  >
    <template #title>
      <div class="agent-modal-titlebar">
        <div v-if="!editingAgentId" class="create-dialog-heading">
          <span class="create-dialog-icon"><Bot :size="18" aria-hidden="true" /></span>
          <span class="agent-modal-title">{{ agentModalTitle }}</span>
        </div>
        <span v-else class="agent-modal-title">{{ agentModalTitle }}</span>
        <div v-if="!editingAgentId" class="create-dialog-header-actions">
          <span class="create-dialog-location">{{ createAdvancedOpen ? '高级配置 · 可选' : '基本信息' }}</span>
          <a-button type="text" class="create-dialog-close" aria-label="关闭创建弹窗" :disabled="saving || agentIconUploading || resourceCreationOpen" @click="closeAgentModal"><X :size="18" aria-hidden="true" /></a-button>
        </div>
        <div class="agent-modal-actions" v-else-if="hasAnyUnsavedChanges">
          <a-button :disabled="saving || boundSkillSaving" @click="closeAgentModal">取消</a-button>
          <a-button type="primary" :loading="saving" :disabled="boundSkillSaving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown || createConfigLoading" @click="saveAgent">
            保存
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
          :disabled="boundSkillSaving"
          @click="selectAgentModalTab(item.key)"
        >
          <span class="nav-item-main">
            <component :is="item.icon" :size="16" />
            <span>{{ item.label }}</span>
          </span>
        </button>
      </aside>

      <div class="agent-modal-main">
        <a-alert v-if="!editingAgentId && (createOutcomeUnknown || createError)" class="create-save-error" :type="createOutcomeUnknown ? 'warning' : 'error'" show-icon :message="createOutcomeUnknown ? '创建结果尚未确认，请取消并刷新列表核对。' : createError" />
        <a-alert
          v-if="shareConfigNeedsRepair"
          type="warning"
          show-icon
          role="alert"
          message="共享配置无效。已暂设为仅所有者，请检查共享范围并保存。"
        />
        <section v-show="editingAgentId ? agentModalActiveTab === 'basic' : !createAdvancedOpen" class="agent-modal-section">
          <div class="agent-profile-header">
            <div class="agent-icon-preview" aria-label="智能体图标与名称">
              <div class="agent-profile-main">
                <div class="agent-avatar-column">
                <a-upload
                  :show-upload-list="false"
                  :before-upload="beforeAgentIconUpload"
                  :disabled="agentIconUploading || saving || resourceCreationOpen"
                  accept="image/*"
                >
                  <div
                    class="agent-icon-upload"
                    :class="{
                      uploading: agentIconUploading
                    }"
                  >
                    <FallbackAvatar
                      :src="agentForm.icon"
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
                <a-popover
                  v-if="!editingAgentId && canEditAgentShareConfig"
                  v-model:open="shareConfigOpen"
                  trigger="click"
                  placement="bottom"
                >
                  <template #content>
                    <div class="agent-share-popover" @keydown.esc.stop="shareConfigOpen = false">
                      <div class="agent-share-dropdown">
                        <!-- 嵌套浮层挂在滚动区外，避免撑大共享面板的滚动范围。 -->
                        <a-config-provider :get-popup-container="(trigger) => trigger.closest('.agent-share-popover')">
                          <div ref="shareConfigHeadingRef" tabindex="-1" class="share-dropdown-heading">
                            <span>访问方式</span>
                            <a-segmented
                              v-model:value="agentForm.visibility"
                              :options="[{ label: '私有', value: 'private' }, { label: '共享', value: 'shared' }]"
                              :disabled="saving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown"
                            />
                          </div>
                          <ShareConfigForm
                            v-if="isAgentShared"
                            ref="agentShareConfigFormRef"
                            v-model="agentShareConfig"
                            :disabled="saving || resourceCreationOpen || createOutcomeUnknown"
                            :auto-select-user-dept="true"
                            :allowed-access-levels="getAgentShareAllowedLevels()"
                          />
                          <p v-else class="share-private-description">仅你可以访问和管理此智能体。</p>
                        </a-config-provider>
                      </div>
                    </div>
                  </template>
                  <button
                    type="button"
                    class="agent-sharing-trigger"
                    :aria-expanded="shareConfigOpen"
                    :disabled="saving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown"
                  >
                    <span>{{ agentSharingLabel }}</span>
                    <Settings2 :size="13" aria-hidden="true" />
                  </button>
                </a-popover>
                </div>
                <div class="agent-icon-preview-text">
                  <label v-if="!editingAgentId" for="agent-profile-name" class="create-field-label">名称 <span aria-hidden="true" class="required-mark">*</span></label>
                  <input
                    id="agent-profile-name"
                    ref="agentNameInputRef"
                    v-model="agentForm.name"
                    class="agent-inline-name-input"
                    type="text"
                    :aria-required="!editingAgentId"
                    placeholder="点击输入智能体名称"
                    aria-label="智能体名称"
                  />
                  <div v-if="!editingAgentId" class="create-profile-details">
                    <div class="create-profile-field">
                      <label for="agent-profile-slug" class="create-field-label">标识 <small>可选</small></label>
                      <input
                        id="agent-profile-slug"
                        v-model="agentForm.slug"
                        class="agent-inline-slug-input"
                        type="text"
                        placeholder="留空自动生成"
                        aria-label="智能体标识"
                      />
                    </div>
                    <div v-if="backendOptions.length > 1" class="create-profile-field">
                      <label for="agent-profile-backend" class="create-field-label">智能体后端</label>
                      <a-select
                        id="agent-profile-backend"
                        v-model:value="agentForm.backend_id"
                        class="agent-backend-select"
                        :options="backendOptions"
                        :disabled="saving || resourceCreationOpen"
                      />
                    </div>
                  </div>
                  <span v-else class="agent-inline-slug">{{
                    agentForm.slug || editingAgentId
                  }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="modal-form">
            <label class="form-label full-width">
              <span>描述</span>
              <a-textarea
                v-model:value="agentForm.description"
                class="agent-description-textarea"
                :rows="3"
                placeholder="可选"
              />
            </label>
          </div>

          <div v-if="editingAgentId && canEditAgentShareConfig" class="share-config-block">
            <div ref="shareConfigHeadingRef" tabindex="-1" class="section-heading">
              <a-checkbox
                v-if="canToggleAgentSharing"
                :checked="isAgentShared"
                :disabled="saving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown"
                @change="agentForm.visibility = $event.target.checked ? 'shared' : 'private'"
              >共享此 Agent</a-checkbox>
              <span v-else>共享权限</span>
            </div>
            <CollapseTransition>
              <div v-if="isAgentShared" class="agent-sharing-options">
                <ShareConfigForm
                  ref="agentShareConfigFormRef"
                  v-model="agentShareConfig"
                  :disabled="saving || resourceCreationOpen || createOutcomeUnknown"
                  :auto-select-user-dept="true"
                  :allowed-access-levels="getAgentShareAllowedLevels()"
                />
              </div>
            </CollapseTransition>
          </div>

          <div v-if="!editingAgentId" class="create-resources">
            <div class="create-resource-row">
              <div class="form-label">
                <span>专属 Skill <small>可选</small></span>
                <span class="create-resource-hint">导入操作指南、脚本与参考资料，创建后可继续编辑。<template v-if="!createSkillFile">单 Skill ZIP，最多 10 MiB</template></span>
                <div v-if="createSkillFile" class="create-skill-selection">
                  <span class="create-skill-filename" :title="createSkillFile.name">{{ createSkillFile.name }}</span>
                  <a-button type="text" size="small" :disabled="saving" @click="createSkillFile = null">移除</a-button>
                </div>
              </div>
              <div class="create-skill-upload">
                <a-upload :show-upload-list="false" :before-upload="beforeCreateSkillUpload" :disabled="saving" accept=".zip">
                  <a-button class="lucide-icon-btn" :disabled="saving"><FileArchive :size="14" aria-hidden="true" />{{ createSkillFile ? '更换 ZIP' : '选择 ZIP' }}</a-button>
                </a-upload>
              </div>
            </div>
          </div>
        </section>

        <section v-if="editingAgentId && agentModalActiveTab === 'skill'" class="agent-modal-section">
          <AgentBoundSkillPanel :key="editingAgentId" :agent-slug="editingAgentId" @navigate="navigateToSkill" @busy="boundSkillSaving = $event" />
        </section>

        <section
          v-if="editingAgentId || createAdvancedOpen || Object.keys(createConfigItems).length"
          v-show="editingAgentId ? isRuntimeAgentModalTab(agentModalActiveTab) : createAdvancedOpen"
          ref="createConfigSectionRef"
          :tabindex="editingAgentId ? null : -1"
          :aria-label="editingAgentId ? null : '高级配置'"
          class="agent-modal-section runtime-section"
        >
          <div v-if="createConfigError" class="create-resource-error" role="alert">
            <span>{{ createConfigError }}</span><a-button size="small" type="link" @click="loadCreateConfig">重试</a-button>
          </div>
          <a-spin :spinning="createConfigLoading">
            <AgentRuntimeConfigForm
              v-if="editingAgentId || Object.keys(runtimeConfigItems).length || (!createConfigLoading && !createConfigError)"
              ref="runtimeFormRef"
              v-model="runtimeConfig"
              :configurable-items="runtimeConfigItems"
              :readonly="runtimeConfigReadonly"
              :creatable-resource-kinds="creatableResourceKinds"
              :segment="editingAgentId ? runtimeConfigSegment : 'all'"
              :show-segmented="false"
              @refresh="refreshRuntimeOptions"
              @manage-resource="manageRuntimeResource"
            >
              <template #resource-actions="{ field, kind }">
                <a-button v-if="field === 'mcps' && userStore.isSuperAdmin" type="text" size="small" class="inline-action-btn lucide-icon-btn" :disabled="runtimeConfigReadonly" @click="openMcpCreate"><Plus :size="14" aria-hidden="true" />新增连接器</a-button>
                <a-upload v-if="field === 'skills' && kind === 'skills' && userStore.isAdmin" class="resource-create-upload" :show-upload-list="false" :before-upload="beforeSharedSkillUpload" :disabled="runtimeConfigReadonly" accept=".zip">
                  <a-button type="text" size="small" class="inline-action-btn lucide-icon-btn" :loading="sharedSkillPreparing" :disabled="runtimeConfigReadonly"><Plus :size="14" aria-hidden="true" />创建共享 Skill</a-button>
                </a-upload>
              </template>
            </AgentRuntimeConfigForm>
          </a-spin>
        </section>
      </div>
    </div>
    <footer v-if="!editingAgentId" class="create-dialog-footer">
      <a-button v-if="createAdvancedOpen" type="text" class="create-back-button" :disabled="saving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown" @click="toggleCreateAdvanced"><ArrowLeft :size="14" aria-hidden="true" />基本信息</a-button>
      <span v-else class="create-footer-hint">模型与工具可稍后配置</span>
      <div class="agent-modal-actions">
        <a-button :disabled="saving || agentIconUploading || resourceCreationOpen" @click="closeAgentModal">取消</a-button>
        <a-button v-if="!createAdvancedOpen" class="create-next-button" :disabled="saving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown" :aria-expanded="createAdvancedOpen" @click="toggleCreateAdvanced">高级配置<ArrowRight :size="14" aria-hidden="true" /></a-button>
        <a-button type="primary" :loading="saving" :disabled="boundSkillSaving || agentIconUploading || resourceCreationOpen || createOutcomeUnknown || createConfigLoading" @click="saveAgent">创建</a-button>
      </div>
    </footer>
  </a-modal>
  <McpFormModal v-model:open="mcpCreateOpen" @submitted="handleMcpCreated" />
  <SkillInstallFlowModal :open="sharedSkillOpen" :flow="sharedSkillFlow" target="shared" @close="sharedSkillOpen = false" @completed="handleSharedSkillCreated" />
</template>

<style lang="less" scoped>
.create-dialog-heading { display: flex; align-items: center; gap: 12px; min-width: 0; }
.create-dialog-icon { display: flex; align-items: center; justify-content: center; width: 36px; height: 36px; flex-shrink: 0; border-radius: 8px; background: var(--gray-100); color: var(--gray-700); }
.create-dialog-header-actions { display: flex; align-items: center; gap: 12px; }
.create-dialog-location { padding: 4px 10px; border-radius: 999px; background: var(--gray-100); color: var(--gray-700); font-size: 12px; font-weight: 400; white-space: nowrap; }
.create-dialog-close { display: flex; align-items: center; justify-content: center; width: 36px; height: 36px; padding: 0; }
.create-dialog-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 24px; border-top: 1px solid var(--gray-150); background: var(--gray-0); }
.create-footer-hint { color: var(--gray-600); font-size: 12px; }
.create-back-button, .create-next-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.create-save-error { margin-bottom: 16px; }
.create-field-label { display: block; color: var(--gray-800); font-size: 12px; line-height: 18px; small { margin-left: 4px; color: var(--gray-600); font-weight: 400; } }
.required-mark { color: var(--color-error-500); }
.create-mode {
  .agent-modal-main { padding: 22px 18px 24px 24px; }
  .agent-icon-preview { align-items: flex-start; }
  .agent-profile-main { align-items: flex-start; flex: 1; gap: 16px; }
  .agent-icon-preview-text { flex: 1; gap: 8px; }
  .agent-icon-upload { margin-top: 0; }
  .agent-inline-name-input, .agent-inline-slug-input { width: 100%; min-height: 36px; padding: 7px 10px; border-color: var(--gray-200); border-radius: 6px; background: var(--gray-0); font-size: 14px; }
  .agent-inline-name-input { font-weight: 500; }
  .agent-inline-slug-input { color: var(--gray-700); }
  .agent-backend-select { width: 100%; margin: 0; :deep(.ant-select-selector) { min-height: 36px; background: var(--gray-0) !important; } :deep(.ant-select-selection-item) { font-weight: 400; } }
}
.create-profile-details { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 16px; margin-top: 8px; }
.create-profile-details:has(> .create-profile-field:only-child) { grid-template-columns: minmax(0, 1fr); }
.create-profile-field { display: grid; gap: 8px; min-width: 0; }
.agent-avatar-column { display: flex; flex-direction: column; align-items: center; gap: 10px; flex-shrink: 0; }
.agent-sharing-trigger { display: inline-flex; align-items: center; gap: 4px; padding: 3px 5px; border: 0; border-radius: 6px; background: var(--gray-100); color: var(--gray-700); font-size: 12px; white-space: nowrap; cursor: pointer; &:hover { background: var(--gray-200); } &:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; } &:disabled { cursor: not-allowed; opacity: .5; } }
.agent-share-popover { position: relative; }
.agent-share-dropdown { position: relative; width: min(320px, calc(100vw - 48px)); max-height: 65vh; overflow-y: auto; }
.share-dropdown-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; color: var(--gray-800); font-size: 13px; }
.share-private-description { margin: 0; color: var(--gray-600); font-size: 12px; line-height: 1.6; }
.create-resources {
  display: grid;
  gap: 16px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--gray-200);
  .form-label { display: grid; gap: 8px; }
  small { margin-left: 6px; color: var(--gray-600); font-weight: 400; }
}
.create-resource-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 16px; }
.create-resource-hint { font-size: 12px; line-height: 1.6; color: var(--gray-600); }
.create-skill-upload { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.create-skill-upload, .resource-create-upload {
  :deep(.ant-upload) { display: inline-flex; align-items: center; }
  :deep(.inline-action-btn) {
    color: var(--gray-600);
    &:hover { color: var(--gray-900); }
  }
}
.create-skill-selection { display: flex; align-items: center; gap: 8px; min-width: 0; :deep(.ant-btn) { flex-shrink: 0; } }
.create-skill-filename { min-width: 0; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: var(--gray-700); }
.create-resource-error { display: flex; align-items: center; gap: 8px; color: var(--color-error-500); font-size: 12px; }
.agent-modal-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  min-height: 32px;
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
    height: min(62vh, 540px);
    min-height: 0;
    max-height: calc(100dvh - 200px);

    .runtime-section { min-height: 0; }
  }
}

.agent-modal-sidebar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  padding: 8px;
  overflow-y: auto;
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

.agent-modal-main {
  min-width: 0;
  min-height: 0;
  overflow: hidden auto;
  overscroll-behavior: contain;
  padding: 12px 18px 24px 8px;
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
  margin-bottom: 32px;
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
  &.uploading .agent-icon-mask {
    opacity: 1;
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

.agent-sharing-options {
  padding-top: 12px;
}

@media (prefers-reduced-motion: reduce) {
  .agent-sharing-options {
    transition: none !important;
  }
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
  .agent-icon-preview {
    flex-direction: column;
    align-items: stretch;
  }

  .agent-profile-main {
    width: 100%;
  }

  .agent-modal-content {
    grid-template-columns: 1fr;
    height: min(78vh, 680px);

    &:not(.without-sidebar) {
      grid-template-rows: auto minmax(0, 1fr);
    }

    &.create-mode {
      height: min(65vh, 540px);
      max-height: calc(100dvh - 210px);
      .agent-icon-upload { margin-top: 0; }
    }
  }

  .agent-modal-nav-item {
    flex: 0 0 auto;
    width: auto;
    white-space: nowrap;
  }

  .agent-modal-sidebar {
    flex-direction: row;
    overflow-x: auto;
  }
  .create-dialog-heading { gap: 8px; }
  .create-dialog-location { display: none; }
  .create-dialog-close { width: 44px; height: 44px; }
  .create-dialog-footer { flex-wrap: wrap; gap: 8px; padding: 12px 16px; .agent-modal-actions { margin-left: auto; } :deep(.ant-btn) { min-height: 44px; } }
  .create-footer-hint { display: none; }
  .create-back-button { padding-left: 0; }
}

@media (max-width: 600px) {
  .create-profile-details { grid-template-columns: minmax(0, 1fr); }
  .create-mode .agent-icon-preview-text { display: contents; }
  .create-mode .agent-profile-main { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 8px 16px; }
  .create-mode .agent-avatar-column { grid-row: 1 / 3; align-self: start; }
  .create-mode .create-profile-details { grid-column: 1 / -1; margin-top: 8px; }
  .create-mode .agent-inline-name-input, .create-mode .agent-inline-slug-input, .create-mode .agent-backend-select :deep(.ant-select-selector), .create-skill-upload :deep(.ant-btn) { min-height: 40px; }
}

:global(.agent-edit-modal .ant-modal-content) {
  overflow: hidden;
  padding: 0;
}

:global(:root.dark .agent-edit-modal .ant-modal-content) {
  border: 0;
  box-shadow: none;
}

:global(.agent-edit-modal .ant-modal-header) {
  margin: 0;
  padding: 10px 24px;
  border-bottom: 1px solid var(--gray-150);
}

:global(.agent-edit-modal:not(.create-agent-modal) .ant-modal-header) {
  padding: 16px 24px;
  border-bottom: 0;
}

:global(.agent-edit-modal .ant-modal-title) {
  width: 100%;
}

:global(.agent-edit-modal .ant-modal-body) {
  padding: 0;
}
:global(.create-agent-modal .ant-modal-header) { padding: 10px 24px; }
@media (max-width: 768px) {
  :global(.create-agent-modal .ant-modal-header) { padding: 8px 16px; }
}
</style>
