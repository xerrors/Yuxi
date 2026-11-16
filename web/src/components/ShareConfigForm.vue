<template>
  <div class="share-config-form" :class="{ disabled }">
    <a-alert
      v-if="hasManageScopeViolation"
      type="warning"
      show-icon
      message="当前管理权限范围大于读取权限范围，请调整后再保存。"
    />

    <section v-for="scope in scopeOptions" :key="scope.key" class="permission-scope-section">
      <div class="permission-scope-header" :class="{ 'has-content': Boolean(scopes[scope.key]) }">
        <h4>{{ scope.title }}</h4>
        <a-switch
          size="small"
          v-if="scope.key !== 'read_scope' || !requireReadScope"
          :checked="Boolean(scopes[scope.key])"
          :aria-label="`${scope.title}${scopes[scope.key] ? '已开启' : '已关闭'}`"
          :disabled="disabled || (scope.key === 'read_scope' && requireReadScope)"
          @change="(enabled) => toggleScope(scope.key, enabled)"
        />
      </div>
      <template v-if="scopes[scope.key]">
        <div
          class="share-mode-cards"
          :class="`active-${scopes[scope.key].access_level}`"
          role="radiogroup"
          :aria-label="scope.title"
        >
          <div
            v-for="option in shareModeOptions"
            :key="option.value"
            role="radio"
            class="share-mode-card"
            :class="{ active: scopes[scope.key].access_level === option.value }"
            :aria-checked="scopes[scope.key].access_level === option.value"
            :tabindex="!disabled && scopes[scope.key].access_level === option.value ? 0 : -1"
            @click="setAccessLevel(scope.key, option.value)"
            @keydown.enter.prevent="setAccessLevel(scope.key, option.value)"
            @keydown.space.prevent="setAccessLevel(scope.key, option.value)"
          >
            <div class="card-main">
              <div class="card-header">
                <div class="card-icon-wrapper" aria-hidden="true">
                  <component :is="option.icon" class="card-icon" :size="20" />
                </div>
                <div class="card-title">{{ option.title }}</div>
                <div
                  v-if="
                    scopes[scope.key].access_level === option.value && option.value !== 'global'
                  "
                  class="card-action"
                  @click.stop
                >
                  <a-dropdown
                    :trigger="['click']"
                    placement="bottomRight"
                    overlay-class-name="share-selection-popover"
                  >
                    <a-button
                      size="small"
                      class="select-action lucide-icon-btn"
                      :aria-label="option.value === 'department' ? '选择部门' : '选择用户'"
                      :disabled="disabled"
                    >
                      <UserPlus class="select-action-icon" :size="14" />
                      <span class="access-count">{{
                        getAccessCount(scope.key, option.value)
                      }}</span>
                    </a-button>
                    <template #overlay>
                      <div class="selection-dropdown" @mousedown.stop @click.stop>
                        <div class="selection-dropdown-header">
                          <div class="selection-dropdown-title">
                            {{ scope.key === 'manage_scope' ? '可管理' : '可读取'
                            }}{{ option.value === 'department' ? '部门' : '用户' }}
                          </div>
                          <div class="selection-dropdown-subtitle">
                            {{ getAccessSummary(scope.key, option.value) }}
                          </div>
                        </div>
                        <a-input
                          v-model:value="selectionSearch[scope.key][option.value]"
                          size="small"
                          allow-clear
                          class="selection-search"
                          :placeholder="option.value === 'department' ? '搜索部门' : '搜索用户'"
                          @mousedown.stop
                          @click.stop
                        />
                        <div
                          v-if="getSelectionOptions(scope.key, option.value).length"
                          class="selection-list"
                        >
                          <div
                            v-for="item in getSelectionOptions(scope.key, option.value)"
                            :key="item.value"
                            role="checkbox"
                            :aria-checked="isSelected(scope.key, option.value, item.value)"
                            tabindex="0"
                            class="selection-item"
                            :class="{ selected: isSelected(scope.key, option.value, item.value) }"
                            @mousedown.stop
                            @click.stop="
                              toggleSelection(
                                scope.key,
                                option.value,
                                item.value,
                                !isSelected(scope.key, option.value, item.value)
                              )
                            "
                            @keydown.enter.prevent="
                              toggleSelection(
                                scope.key,
                                option.value,
                                item.value,
                                !isSelected(scope.key, option.value, item.value)
                              )
                            "
                            @keydown.space.prevent="
                              toggleSelection(
                                scope.key,
                                option.value,
                                item.value,
                                !isSelected(scope.key, option.value, item.value)
                              )
                            "
                          >
                            <span class="selection-item-content">
                              <a-checkbox
                                :checked="isSelected(scope.key, option.value, item.value)"
                                @click.stop
                                @change="
                                  toggleSelection(
                                    scope.key,
                                    option.value,
                                    item.value,
                                    $event.target.checked
                                  )
                                "
                              />
                              <span class="selection-label">{{ item.label }}</span>
                            </span>
                          </div>
                        </div>
                        <div v-else class="selection-empty">暂无可选项</div>
                      </div>
                    </template>
                  </a-dropdown>
                </div>
              </div>
              <div class="card-description">{{ option.description }}</div>
            </div>
          </div>
        </div>
      </template>
    </section>

    <a-alert
      v-if="disabled && disabledReason"
      type="info"
      show-icon
      class="share-disabled-alert"
      :message="disabledReason"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { Building2, Globe, Users, UserPlus } from '@lucide/vue'
import { useUserStore } from '@/stores/user'
import { authApi } from '@/apis/auth_api'
import { departmentApi } from '@/apis/department_api'

const userStore = useUserStore()
const departments = ref([])
const users = ref([])
const syncingFromProps = ref(false)

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
    default: () => ({
      version: 2,
      read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
      manage_scope: null
    })
  },
  autoSelectUserDept: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  disabledReason: { type: String, default: '' },
  requireReadScope: { type: Boolean, default: false },
  allowedAccessLevels: {
    type: Array,
    default: () => ['global', 'department', 'user']
  }
})

const emit = defineEmits(['update:modelValue'])

const scopeOptions = [
  { key: 'read_scope', title: '读取权限' },
  { key: 'manage_scope', title: '共享管理权限（包含读取权限）' }
]

const baseShareModeOptions = [
  { value: 'global', title: '全局共享', description: '所有用户都可以访问', icon: Globe },
  {
    value: 'department',
    title: '部门共享',
    description: '选中的部门成员可以访问',
    icon: Building2
  },
  { value: 'user', title: '指定人', description: '选中的用户可以访问', icon: Users }
]

const scopes = reactive({ read_scope: null, manage_scope: null })
const selectionSearch = reactive({
  read_scope: { department: '', user: '' },
  manage_scope: { department: '', user: '' }
})

const currentDepartmentId = computed(() =>
  userStore.departmentId ? Number(userStore.departmentId) : null
)
const currentUserUid = computed(() => userStore.uid || '')
const normalizedAllowedAccessLevels = computed(() => {
  const allowed = props.allowedAccessLevels.filter((level) =>
    ['global', 'department', 'user'].includes(level)
  )
  return allowed.length ? allowed : ['global']
})
const shareModeOptions = computed(() =>
  baseShareModeOptions.filter((option) =>
    normalizedAllowedAccessLevels.value.includes(option.value)
  )
)

const createScope = (scope) => ({
  access_level: scope?.access_level || 'global',
  department_ids: Array.from(
    new Set((scope?.department_ids || []).map(Number).filter(Number.isFinite))
  ),
  user_uids: Array.from(
    new Set((scope?.user_uids || []).map((uid) => String(uid).trim()).filter(Boolean))
  )
})

const normalizeScope = (scope, { includeCurrent = false } = {}) => {
  if (!scope) return null
  const normalized = createScope(scope)
  if (!normalizedAllowedAccessLevels.value.includes(normalized.access_level)) {
    normalized.access_level = normalizedAllowedAccessLevels.value[0]
  }
  if (normalized.access_level === 'global') {
    normalized.department_ids = []
    normalized.user_uids = []
  } else if (normalized.access_level === 'department') {
    normalized.user_uids = []
    if (
      includeCurrent &&
      currentDepartmentId.value &&
      !normalized.department_ids.includes(currentDepartmentId.value)
    ) {
      normalized.department_ids.unshift(currentDepartmentId.value)
    }
  } else {
    normalized.department_ids = []
    if (
      includeCurrent &&
      currentUserUid.value &&
      !normalized.user_uids.includes(currentUserUid.value)
    ) {
      normalized.user_uids.unshift(currentUserUid.value)
    }
  }
  return normalized
}

const isManageScopeWithinRead = (manageScope, readScope = scopes.read_scope) => {
  if (!manageScope || !readScope || readScope.access_level === 'global') return true
  if (manageScope.access_level !== readScope.access_level) return false
  if (readScope.access_level === 'user') {
    return (
      manageScope.access_level === 'user' &&
      manageScope.user_uids.every((uid) => readScope.user_uids.includes(uid))
    )
  }
  if (manageScope.access_level === 'department') {
    return manageScope.department_ids.every((id) => readScope.department_ids.includes(id))
  }
  return true
}

const initConfig = () => {
  syncingFromProps.value = true
  const source = props.modelValue || {}
  const isV2 = source.version === 2
  const readScope = isV2 ? source.read_scope : source
  scopes.read_scope = normalizeScope(readScope, { includeCurrent: props.autoSelectUserDept })
  scopes.manage_scope = normalizeScope(isV2 ? source.manage_scope : null)
  const hadMissingRequiredRead = props.requireReadScope && !scopes.read_scope
  if (hadMissingRequiredRead) {
    scopes.read_scope = normalizeScope({ access_level: 'global' })
  }
  nextTick(() => {
    syncingFromProps.value = false
    if (hadMissingRequiredRead) emitConfig()
  })
}

const emitConfig = () => {
  emit('update:modelValue', {
    version: 2,
    read_scope: normalizeScope(scopes.read_scope),
    manage_scope: normalizeScope(scopes.manage_scope)
  })
}

const toggleScope = (scopeKey, enabled) => {
  if (props.disabled) return
  const defaultManageLevel = scopes.read_scope?.access_level || 'global'
  scopes[scopeKey] = enabled
    ? normalizeScope({ access_level: scopeKey === 'read_scope' ? 'global' : defaultManageLevel })
    : null
}

const setAccessLevel = (scopeKey, accessLevel) => {
  if (
    props.disabled ||
    !scopes[scopeKey] ||
    !normalizedAllowedAccessLevels.value.includes(accessLevel)
  )
    return
  scopes[scopeKey].access_level = accessLevel
  scopes[scopeKey] = normalizeScope(scopes[scopeKey], {
    includeCurrent: scopeKey === 'read_scope' && props.autoSelectUserDept
  })
}

const departmentOptions = computed(() =>
  departments.value.map((dept) => ({
    label: dept.name,
    value: Number(dept.id)
  }))
)
const userOptions = computed(() =>
  users.value.map((user) => ({
    label: user.department_name ? `${user.username}（${user.department_name}）` : user.username,
    value: user.uid,
    department_id: user.department_id
  }))
)

const getAccessCount = (scopeKey, accessLevel) => {
  const scope = scopes[scopeKey]
  if (accessLevel === 'department') return scope?.department_ids.length || 0
  if (accessLevel === 'user') return scope?.user_uids.length || 0
  return ''
}
const getAccessSummary = (scopeKey, accessLevel) => {
  const scope = scopes[scopeKey]
  if (accessLevel === 'global') return '所有用户可访问'
  if (accessLevel === 'department') return `${scope?.department_ids.length || 0} 个部门可访问`
  return `${scope?.user_uids.length || 0} 个用户可访问`
}
const getSelectionOptions = (scopeKey, accessLevel) => {
  let options = accessLevel === 'department' ? departmentOptions.value : userOptions.value

  const query = selectionSearch[scopeKey][accessLevel].trim().toLowerCase()
  return query ? options.filter((item) => item.label.toLowerCase().includes(query)) : options
}
const isSelected = (scopeKey, accessLevel, value) => {
  const scope = scopes[scopeKey]
  if (!scope) return false
  return accessLevel === 'department'
    ? scope.department_ids.includes(Number(value))
    : scope.user_uids.includes(String(value))
}
const toggleSelection = (scopeKey, accessLevel, value, checked) => {
  if (props.disabled || !scopes[scopeKey]) return
  const scope = scopes[scopeKey]
  if (accessLevel === 'department') {
    scope.department_ids = Array.from(
      new Set(
        checked
          ? [...scope.department_ids, Number(value)]
          : scope.department_ids.filter((id) => id !== Number(value))
      )
    )
  } else {
    scope.user_uids = Array.from(
      new Set(
        checked
          ? [...scope.user_uids, String(value)]
          : scope.user_uids.filter((uid) => uid !== String(value))
      )
    )
  }
}

const loadDepartments = async () => {
  try {
    const result = await departmentApi.getDepartments()
    departments.value = result.departments || result || []
  } catch (error) {
    console.error('加载部门列表失败:', error)
  }
}
const loadUsers = async () => {
  try {
    users.value = await authApi.getUserAccessOptions()
  } catch (error) {
    console.error('加载用户列表失败:', error)
  }
}

watch(() => props.modelValue, initConfig, { deep: true })
watch(normalizedAllowedAccessLevels, initConfig)
watch(
  scopes,
  () => {
    if (!syncingFromProps.value) emitConfig()
  },
  { deep: true }
)

const validateScope = (scope, title) => {
  if (!scope || scope.access_level === 'global') return { valid: true, message: '' }
  if (scope.access_level === 'department' && !scope.department_ids.length) {
    return { valid: false, message: `${title}至少需要选择一个部门` }
  }
  if (scope.access_level === 'user' && !scope.user_uids.length) {
    return { valid: false, message: `${title}至少需要选择一个用户` }
  }
  return { valid: true, message: '' }
}

const validate = () => {
  const readResult = validateScope(scopes.read_scope, '读取权限')
  if (!readResult.valid) return readResult
  const manageResult = validateScope(scopes.manage_scope, '管理权限')
  if (!manageResult.valid) return manageResult
  if (!isManageScopeWithinRead(scopes.manage_scope)) {
    return { valid: false, message: '管理权限必须包含在读取权限范围内' }
  }
  return manageResult
}

const hasManageScopeViolation = computed(() =>
  Boolean(scopes.manage_scope && !isManageScopeWithinRead(scopes.manage_scope))
)

onMounted(() => {
  initConfig()
  loadDepartments()
  loadUsers()
})

defineExpose({ scopes, validate })
</script>

<style lang="less" scoped>
.share-config-form {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--gray-150);
}

.permission-scope-section {
  padding: 14px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.permission-scope-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  &.has-content {
    margin-bottom: 12px;
  }
}

.permission-scope-header h4 {
  margin: 0;
  color: var(--gray-800);
  font-size: 14px;
}

.share-mode-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.share-mode-card {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
  cursor: pointer;
}

.share-mode-card:hover,
.share-mode-card.active {
  border-color: var(--main-color);
  background: var(--main-10);
}

.card-header,
.card-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-main {
  align-items: stretch;
  flex-direction: column;
}

.card-title {
  flex: 1;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  height: 20px;
  display: flex;
  align-items: center;
}

.card-description {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.45;
}

.card-icon-wrapper {
  display: inline-flex;
  align-items: center;
  color: var(--main-color);
  height: 20px;
}

.card-action {
  margin-left: auto;
  display: flex;
  align-items: center;
  height: 20px;

  :deep(.ant-btn) {
    height: 20px;
    padding: 0 8px;
    font-size: 12px;
    line-height: 1;
  }
}

.access-count {
  margin-left: 3px;
}

.share-disabled-alert {
  margin-top: 2px;
}

.selection-dropdown {
  width: 280px;
  padding: 10px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
}

.selection-dropdown-header {
  margin-bottom: 8px;
}

.selection-dropdown-title {
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
}

.selection-dropdown-subtitle,
.selection-empty {
  color: var(--gray-500);
  font-size: 12px;
}

.selection-search {
  margin-bottom: 8px;
}

.selection-list {
  max-height: 240px;
  overflow-y: auto;
}

.selection-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 4px;
  border-radius: 6px;
  cursor: pointer;
}

.selection-item:hover,
.selection-item.selected {
  background: var(--main-10);
}

.selection-item-content {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.selection-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .share-mode-cards {
    grid-template-columns: 1fr;
  }
}
</style>
