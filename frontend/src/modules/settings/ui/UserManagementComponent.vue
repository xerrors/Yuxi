<template>
  <div class="user-management settings-page">
    <!-- 头部区域 -->
    <div class="settings-header">
      <div class="settings-header-copy">
        <div class="settings-section-title">用户管理</div>
        <p class="settings-section-description">
          管理系统用户，请谨慎操作。删除用户后该用户将无法登录系统。
        </p>
      </div>
      <div class="settings-header-actions">
        <a-button
          @click="handleRefresh"
          :loading="userManagement.refreshing"
          title="刷新"
          class="yuxi-refresh-button lucide-icon-btn"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ 'yuxi-rotate': userManagement.refreshing }" />
          </template>
        </a-button>
        <a-button type="primary" @click="showAddUserModal" class="lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          添加用户
        </a-button>
      </div>
    </div>

    <div class="user-table-toolbar">
      <div class="user-table-filters">
        <a-input
          v-model:value="userManagement.searchKeyword"
          class="search-input"
          placeholder="搜索用户名 / ID / 手机号"
          allow-clear
        >
          <template #prefix><Search :size="16" /></template>
        </a-input>
        <label for="managed-user-kind" class="filter-label">用户类型</label>
        <a-select id="managed-user-kind" v-model:value="userManagement.userKindFilter" class="user-kind-select">
          <a-select-option value="human">系统用户</a-select-option>
          <a-select-option value="end_user">APP 终端用户</a-select-option>
          <a-select-option value="all">全部用户类型</a-select-option>
        </a-select>
      </div>
      <div v-if="selectedUserIds.length" class="user-selection-actions" role="group" aria-label="所选用户操作">
        <a-button type="text" @click="selectedUserIds = []">清空选择</a-button>
        <a-button
          danger
          class="lucide-icon-btn"
          :disabled="selectionUnavailable"
          @click="deleteSelectedUsers"
        >
          <Trash2 :size="14" />
          批量删除 ({{ selectedUserIds.length }})
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="settings-management-content">
      <a-spin :spinning="userManagement.loading">
        <div v-if="userManagement.error" class="settings-error">
          <a-alert type="error" :message="userManagement.error" show-icon />
        </div>

        <template v-if="userManagement.users.length > 0 || hasActiveFilters">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="userManagement.users"
              :columns="columns"
              :rowKey="(record) => record.id"
              :row-selection="userSelection"
              :pagination="false"
              table-layout="fixed"
              :scroll="{ x: 760 }"
            >
              <template #headerCell="{ column }">
                <template v-if="column.key === 'role' && showHeaderFilter">
                  <div class="header-filter-cell">
                    <span>角色</span>
                    <a-dropdown :trigger="['click']" placement="bottomRight" :open="filterOpen.role" @open-change="(open) => setFilterOpen('role', open)">
                      <template #overlay>
                        <div class="yuxi-header-filter-panel" @click.stop>
                          <a-checkbox-group
                            v-model:value="filterDraft.role"
                            class="header-filter-options"
                            :options="roleFilterOptions"
                          />
                          <div class="header-filter-actions">
                            <button type="button" class="header-filter-clear" :disabled="!filterDraft.role.length" @click="filterDraft.role = []">清空</button>
                            <a-button size="small" @click="filterOpen.role = false">取消</a-button>
                            <a-button type="primary" size="small" @click="applyFilter('role')">确认</a-button>
                          </div>
                        </div>
                      </template>
                      <button
                        type="button"
                        class="header-filter-btn"
                        :class="{ active: userManagement.roleFilterValues.length }"
                        :aria-label="`筛选角色（已选 ${userManagement.roleFilterValues.length} 项）`"
                        :aria-expanded="filterOpen.role"
                      >
                        <ListFilter :size="14" />
                      </button>
                    </a-dropdown>
                  </div>
                </template>
                <template v-else-if="column.key === 'department' && showHeaderFilter">
                  <div class="header-filter-cell">
                    <span>所属部门/实例</span>
                    <a-dropdown
                      :trigger="['click']"
                      placement="bottomRight"
                      :open="filterOpen.department"
                      @open-change="(open) => setFilterOpen('department', open)"
                    >
                      <template #overlay>
                        <div class="yuxi-header-filter-panel department-filter-panel" @click.stop>
                          <template v-if="departmentFilterOptions.length">
                            <a-input
                              v-if="departmentFilterOptions.length > 8"
                              v-model:value="departmentFilterKeyword"
                              class="yuxi-header-filter-search"
                              size="small"
                              placeholder="搜索部门"
                              allow-clear
                            />
                            <a-checkbox-group
                              v-model:value="filterDraft.department"
                              class="header-filter-options"
                            >
                              <!-- 保留隐藏项的注册，避免 Ant 在勾选搜索结果时丢弃此前已选部门。 -->
                              <a-checkbox
                                v-for="option in departmentFilterOptions"
                                v-show="visibleDepartmentOptions.includes(option)"
                                :key="option.value"
                                :value="option.value"
                              >
                                {{ option.label }}
                              </a-checkbox>
                            </a-checkbox-group>
                            <div
                              v-if="departmentFilterOptions.length && !visibleDepartmentOptions.length"
                              class="header-filter-empty"
                            >
                              没有匹配的部门
                            </div>
                          </template>
                          <div v-else class="header-filter-empty">暂无可筛选的部门</div>
                          <div class="header-filter-actions">
                            <button type="button" class="header-filter-clear" :disabled="!filterDraft.department.length" @click="filterDraft.department = []">清空</button>
                            <a-button size="small" @click="filterOpen.department = false">取消</a-button>
                            <a-button type="primary" size="small" @click="applyFilter('department')">确认</a-button>
                          </div>
                        </div>
                      </template>
                      <button
                        type="button"
                        class="header-filter-btn"
                        :class="{ active: userManagement.departmentFilterValues.length }"
                        :aria-label="`筛选部门（已选 ${userManagement.departmentFilterValues.length} 项）`"
                        :aria-expanded="filterOpen.department"
                      >
                        <ListFilter :size="14" />
                      </button>
                    </a-dropdown>
                  </div>
                </template>
              </template>
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'user'">
                  <div class="user-table-cell">
                    <FallbackAvatar
                      :src="record.avatar"
                      :name="record.username"
                      :seed="record.uid || record.username"
                      kind="user"
                      :size="28"
                      shape="circle"
                      :alt="record.username"
                      class="user-avatar"
                    />
                    <a-popover trigger="click" title="用户信息">
                      <template #content>
                        <div class="user-identity-details">
                          <div>用户名：{{ record.username }}</div>
                          <div>ID：{{ record.uid }}</div>
                          <div v-if="record.user_kind === 'end_user'">APP 实例：{{ record.app_id }}</div>
                        </div>
                      </template>
                      <button type="button" class="user-meta" aria-label="查看完整用户信息">
                        <span class="user-name">{{ record.username }}</span>
                        <span v-if="record.uid" class="user-uid">ID: {{ record.uid }}</span>
                      </button>
                    </a-popover>
                  </div>
                </template>
                <template v-if="column.key === 'role'">
                  <span class="role-badge" :class="record.role">
                    <UserLock v-if="record.role === 'superadmin'" :size="12" />
                    <UserStar v-else-if="record.role === 'admin'" :size="12" />
                    <User v-else :size="12" />
                    <span>{{ getRoleDisplayName(record.role) }}</span>
                  </span>
                </template>
                <template v-if="column.key === 'department'">
                  <span class="dept-text" :title="record.user_kind === 'end_user' ? record.app_id : record.department_name">
                    {{ record.user_kind === 'end_user' ? record.app_id : record.department_name || '-' }}
                  </span>
                </template>
                <template v-if="column.key === 'phone'">
                  <span class="phone-text">{{ record.phone_number || '-' }}</span>
                </template>
                <template v-if="column.key === 'lastLogin'">
                  <span class="time-text">{{ formatTime(record.last_login) }}</span>
                </template>
                <template v-if="column.key === 'action'">
                  <a-space :size="4">
                    <a-tooltip :title="record.user_kind === 'end_user' ? 'APP 终端用户不支持编辑' : '编辑用户'">
                      <a-button
                        type="text"
                        size="small"
                        class="yuxi-table-action lucide-icon-btn"
                        :disabled="isUserEditDisabled(record)"
                        @click="showEditUserModal(record)"
                      >
                        <SquarePen :size="14" />
                      </a-button>
                    </a-tooltip>
                    <a-tooltip
                      :title="
                        isUserDeleteDisabled(record) ? '无权删除该用户' : '删除用户'
                      "
                    >
                      <a-button
                        type="text"
                        size="small"
                        danger
                        :disabled="isUserDeleteDisabled(record)"
                        class="yuxi-table-action lucide-icon-btn"
                        @click="confirmDeleteUser(record)"
                      >
                        <Trash2 :size="14" />
                      </a-button>
                    </a-tooltip>
                  </a-space>
                </template>
              </template>
            </a-table>
          </div>

          <div v-if="userManagement.total > userManagement.pageSize" class="pagination-section">
            <a-pagination
              :current="userManagement.currentPage"
              :page-size="userManagement.pageSize"
              :total="userManagement.total"
              :page-size-options="['20', '50', '100']"
              show-size-changer
              size="small"
              @change="handlePageChange"
            />
          </div>
        </template>

        <div v-else class="settings-empty">
          <a-empty :description="hasActiveFilters ? '没有匹配的用户' : '暂无用户数据'" />
        </div>
      </a-spin>
    </div>

    <!-- 用户表单模态框 -->
    <a-modal
      v-model:open="userManagement.modalVisible"
      :title="userManagement.modalTitle"
      @ok="handleUserFormSubmit"
      :confirmLoading="userManagement.loading"
      @cancel="userManagement.modalVisible = false"
      :maskClosable="false"
      width="480px"
      class="user-modal"
    >
      <a-form layout="vertical" class="user-form">
        <a-form-item label="用户名" required>
          <a-input
            v-model:value="userManagement.form.username"
            ref="usernameInputRef"
            placeholder="2-20 个字符，支持中文、英文、数字和下划线"
            :status="userManagement.form.usernameError ? 'error' : undefined"
            :aria-invalid="Boolean(userManagement.form.usernameError)"
            aria-describedby="username-help username-error"
            @blur="validateAndGenerateUid"
            :maxlength="20"
          />
          <div id="username-error" v-if="userManagement.form.usernameError" class="yuxi-form-error" role="alert">
            {{ userManagement.form.usernameError }}
          </div>
          <div
            id="username-help"
            class="yuxi-form-help"
          >
            <template v-if="userManagement.editMode">ID: {{ userManagement.form.generatedUid }}，修改用户名不会改变 ID。</template>
            <template v-else-if="userManagement.form.generatedUid">ID: {{ userManagement.form.generatedUid }}，将用于登录。</template>
            <template v-else>ID 将根据用户名自动生成，用于登录；同名用户的 ID 各不相同。</template>
          </div>
        </a-form-item>

        <!-- 手机号字段 -->
        <a-form-item label="手机号">
          <a-input
            v-model:value="userManagement.form.phoneNumber"
            placeholder="请输入手机号（可选，可用于登录）"
            :maxlength="11"
          />
          <div v-if="userManagement.form.phoneError" class="yuxi-form-error">
            {{ userManagement.form.phoneError }}
          </div>
        </a-form-item>

        <template v-if="userManagement.editMode && userStore.isSuperAdmin">
          <div class="password-toggle">
            <a-checkbox v-model:checked="userManagement.displayPasswordFields">
              修改密码
            </a-checkbox>
          </div>
        </template>

        <template v-if="!userManagement.editMode || userManagement.displayPasswordFields">
          <a-form-item label="密码" required>
            <a-input-password
              v-model:value="userManagement.form.password"
              :placeholder="`请输入密码（至少 ${MIN_PASSWORD_LENGTH} 位）`"
              :minlength="MIN_PASSWORD_LENGTH"
            />
          </a-form-item>

          <a-form-item label="确认密码" required>
            <a-input-password
              v-model:value="userManagement.form.confirmPassword"
              placeholder="请再次输入密码"
            />
          </a-form-item>
        </template>

        <a-form-item v-if="!userManagement.editMode || userStore.isSuperAdmin" label="角色">
          <a-select v-model:value="userManagement.form.role">
            <a-select-option value="user" :disabled="userManagement.editMode && userManagement.originalRole !== 'user'">普通用户</a-select-option>
            <a-select-option value="admin" v-if="userStore.isSuperAdmin" :disabled="userManagement.editMode && userManagement.originalRole === 'superadmin'">管理员</a-select-option>
            <a-select-option value="superadmin" v-if="userStore.isSuperAdmin">系统管理员</a-select-option>
          </a-select>
        </a-form-item>

        <!-- 部门选择器（仅超级管理员可见） -->
        <a-form-item v-if="userStore.isSuperAdmin" label="部门">
          <a-select v-model:value="userManagement.form.departmentId" placeholder="请选择部门">
            <a-select-option
              v-for="dept in departmentManagement.departments"
              :key="dept.id"
              :value="dept.id"
            >
              {{ dept.name }}
            </a-select-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted, onUnmounted, watch, computed } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/modules/identity'
import { authApi, departmentApi } from '@/apis'
import { Plus, SquarePen, Trash2, User, UserLock, UserStar, RefreshCw, Search, ListFilter } from '@lucide/vue'
import { formatDateTime } from '@/shared/lib/time'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '@/modules/identity'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import { confirmManagementBatchDeletion } from '../model/confirmManagementBatchDeletion'

const userStore = useUserStore()

const columns = [
  { title: '用户', key: 'user' },
  { title: '角色', dataIndex: 'role', key: 'role', width: 110 },
  { title: '所属部门/实例', key: 'department', width: '20%' },
  { title: '手机号', dataIndex: 'phone_number', key: 'phone', width: 110 },
  { title: '最后登录', dataIndex: 'last_login', key: 'lastLogin', width: 140 },
  { title: '操作', key: 'action', width: 70, align: 'center' }
]

const getRoleDisplayName = (role) => {
  const map = {
    superadmin: '超级管理员',
    admin: '管理员',
    user: '普通用户'
  }
  return map[role] || role || '普通用户'
}

// 用户管理相关状态
const userManagement = reactive({
  loading: false,
  refreshing: false,
  users: [],
  total: 0,
  searchKeyword: '',
  departmentFilterValues: [],
  roleFilterValues: [],
  userKindFilter: 'human',
  currentPage: 1,
  pageSize: 20,
  error: null,
  modalVisible: false,
  modalTitle: '添加用户',
  editMode: false,
  editUserId: null,
  form: {
    username: '',
    generatedUid: '', // 自动生成的uid
    phoneNumber: '', // 手机号
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色
    departmentId: null, // 部门ID
    usernameError: '', // 用户名错误信息
    phoneError: '' // 手机号错误信息
  },
  displayPasswordFields: true // 编辑时是否显示密码字段
})

// 部门列表（仅超级管理员使用）
const departmentManagement = reactive({
  departments: []
})

const hasActiveFilters = computed(
  () =>
    Boolean(userManagement.searchKeyword.trim()) ||
    userManagement.departmentFilterValues.length > 0 ||
    userManagement.roleFilterValues.length > 0 ||
    userManagement.userKindFilter !== 'human'
)

// APP 终端用户没有部门与产品角色，表头筛选只在系统用户视图提供；切换视图时清空不可见筛选。
const showHeaderFilter = computed(() => userManagement.userKindFilter !== 'end_user')
watch(showHeaderFilter, (visible) => {
  if (visible) return
  userManagement.departmentFilterValues = []
  userManagement.roleFilterValues = []
})

// 打开菜单时复制已应用的筛选，只有确认才发布草稿。
const filterOpen = reactive({ role: false, department: false })
const filterDraft = reactive({ role: [], department: [] })
const setFilterOpen = (kind, open) => {
  if (open) {
    filterDraft[kind] = [...userManagement[`${kind}FilterValues`]]
    if (kind === 'department') departmentFilterKeyword.value = ''
  }
  filterOpen[kind] = open
}
const applyFilter = (kind) => {
  userManagement[`${kind}FilterValues`] = [...filterDraft[kind]]
  filterOpen[kind] = false
}

// 部门候选可能很多，菜单内按关键词本地过滤。
const departmentFilterKeyword = ref('')
const visibleDepartmentOptions = computed(() => {
  const keyword = departmentFilterKeyword.value.trim().toLowerCase()
  if (!keyword) return departmentFilterOptions.value
  return departmentFilterOptions.value.filter((option) => option.label.toLowerCase().includes(keyword))
})

const roleFilterOptions = [
  { label: '超级管理员', value: 'superadmin' },
  { label: '管理员', value: 'admin' },
  { label: '普通用户', value: 'user' }
]

const departmentFilterOptions = computed(() => {
  const options = new Map()

  // 只提供还有用户的部门：无人的部门进入筛选只会制造噪音。
  departmentManagement.departments.forEach((dept) => {
    if (!(dept.user_count > 0)) return
    options.set(String(dept.id), {
      value: String(dept.id),
      label: dept.name
    })
  })

  userManagement.users.forEach((user) => {
    // 部门筛选走后端 department_ids 数字契约，缺 id 的记录（仅名称回退）不可作为筛选项。
    if (user.department_id == null) return

    const value = String(user.department_id)

    if (!options.has(value)) {
      options.set(value, {
        value,
        label: user.department_name || `部门 ${user.department_id}`
      })
    }
  })

  return [...options.values()]
})

// 获取部门列表
const fetchDepartments = async () => {
  if (!userStore.isSuperAdmin) return // 普通管理员不需要获取所有部门列表
  try {
    const departments = await departmentApi.getDepartments()
    departmentManagement.departments = departments
  } catch (error) {
    console.error('获取部门列表失败:', error)
  }
}

const usernameInputRef = ref(null)

/** 提示后端允许的用户名格式，最终校验仍由后端执行。 */
const getUsernameError = (username) => {
  if (!username) return '用户名不能为空'
  if (username.length < 2 || username.length > 20) return '用户名长度必须在 2-20 个字符之间'
  if (!/^[一-龥a-zA-Z0-9_]+$/.test(username)) return '用户名只能包含中文、英文、数字和下划线'
  return ''
}

// 添加验证用户名并生成uid的函数
const validateAndGenerateUid = async () => {
  const username = userManagement.form.username.trim()

  // 清空之前的错误和生成的ID
  userManagement.form.usernameError = ''
  if (!userManagement.editMode) userManagement.form.generatedUid = ''

  if (!username) {
    return
  }

  userManagement.form.usernameError = getUsernameError(username)
  if (userManagement.form.usernameError) return

  // 在编辑模式下，不需要重新生成uid
  if (userManagement.editMode) {
    return
  }

  try {
    const result = await userStore.validateUsernameAndGenerateUid(username)
    if (userManagement.editMode || username !== userManagement.form.username.trim()) return
    userManagement.form.generatedUid = result.uid
  } catch (error) {
    if (userManagement.editMode || username !== userManagement.form.username.trim()) return
    userManagement.form.usernameError = error.message || '用户名验证失败'
  }
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }

  // 中国大陆手机号格式验证
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听密码字段显示状态变化
watch(
  () => userManagement.displayPasswordFields,
  (newVal) => {
    // 当取消显示密码字段时，清空密码输入
    if (!newVal) {
      userManagement.form.password = ''
      userManagement.form.confirmPassword = ''
    }
  }
)

// 监听手机号输入变化
watch(
  () => userManagement.form.phoneNumber,
  (newPhone) => {
    userManagement.form.phoneError = ''

    if (newPhone && !validatePhoneNumber(newPhone)) {
      userManagement.form.phoneError = '请输入正确的手机号格式'
    }
  }
)

const selectedUserIds = ref([])
const selectionUnavailable = computed(() => userManagement.loading || Boolean(userManagement.error))
let filterRequestTimer = null
let latestUserRequest = 0

watch(() => userStore.token, () => {
  selectedUserIds.value = []
})

watch(
  () => [userManagement.searchKeyword, userManagement.departmentFilterValues, userManagement.roleFilterValues, userManagement.userKindFilter],
  () => {
    selectedUserIds.value = []
    userManagement.currentPage = 1
    // 筛选防抖期间保留旧行供展示，停止选择并丢弃旧请求结果。
    userManagement.loading = true
    latestUserRequest += 1
    if (filterRequestTimer) clearTimeout(filterRequestTimer)
    filterRequestTimer = setTimeout(() => fetchUsers(), 250)
  },
  { deep: true }
)

// 格式化时间显示
const formatTime = (timeStr) => formatDateTime(timeStr)

/** APP 终端用户不进入产品账号编辑表单。 */
const isUserEditDisabled = (user) =>
  user.user_kind === 'end_user' ||
  (!userStore.isSuperAdmin && user.role !== 'user')

const isUserDeleteDisabled = (user) =>
  user.id === userStore.userId ||
  (!userStore.isSuperAdmin && user.role !== 'user')

const userSelection = computed(() => ({
  columnWidth: 44,
  selectedRowKeys: selectedUserIds.value,
  preserveSelectedRowKeys: true,
  onChange: (ids) => { selectedUserIds.value = ids },
  getCheckboxProps: (user) => ({
    disabled: selectionUnavailable.value || isUserDeleteDisabled(user),
    'aria-label': `选择用户 ${user.username}`
  })
}))

const fetchUsers = async () => {
  const requestId = ++latestUserRequest
  try {
    userManagement.loading = true
    const pageSize = Number(userManagement.pageSize)
    const response = await authApi.getUsersPage({
      offset: (userManagement.currentPage - 1) * pageSize,
      limit: pageSize,
      search: userManagement.searchKeyword.trim(),
      departmentIds: userManagement.departmentFilterValues,
      roles: userManagement.roleFilterValues,
      userKind: userManagement.userKindFilter
    })
    if (requestId !== latestUserRequest) return

    const maxPage = Math.max(1, Math.ceil(response.total / pageSize))
    if (userManagement.currentPage > maxPage) {
      userManagement.currentPage = maxPage
      await fetchUsers()
      return
    }

    userManagement.users = response.items
    userManagement.total = response.total
    userManagement.error = null
    const blockedIds = new Set(response.items.filter(isUserDeleteDisabled).map(user => user.id))
    selectedUserIds.value = selectedUserIds.value.filter(id => !blockedIds.has(id))
  } catch (error) {
    if (requestId !== latestUserRequest) return
    console.error('获取用户列表失败:', error)
    userManagement.error = '获取用户列表失败'
  } finally {
    if (requestId === latestUserRequest) userManagement.loading = false
  }
}

const handlePageChange = (page, pageSize) => {
  if (filterRequestTimer) clearTimeout(filterRequestTimer)
  userManagement.currentPage = pageSize === userManagement.pageSize ? page : 1
  userManagement.pageSize = pageSize
  fetchUsers()
}

// 刷新用户和部门信息
const handleRefresh = async () => {
  if (userManagement.refreshing) return
  userManagement.refreshing = true
  try {
    await Promise.all([fetchUsers(), fetchDepartments()])
    message.success('刷新成功')
  } catch (error) {
    console.error('刷新失败:', error)
    message.error('刷新失败')
  } finally {
    userManagement.refreshing = false
  }
}

// 打开添加用户模态框
const showAddUserModal = () => {
  userManagement.modalTitle = '添加用户'
  userManagement.editMode = false
  userManagement.editUserId = null
  userManagement.form = {
    username: '',
    generatedUid: '',
    phoneNumber: '',
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色为普通用户
    departmentId: null,
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = true
  userManagement.modalVisible = true
}

// 打开编辑用户模态框
const showEditUserModal = (user) => {
  if (isUserEditDisabled(user)) return

  userManagement.modalTitle = '编辑用户'
  userManagement.editMode = true
  userManagement.editUserId = user.id
  userManagement.originalRole = user.role
  userManagement.form = {
    role: user.role,
    username: user.username,
    generatedUid: user.uid || '', // 编辑模式显示现有的uid
    phoneNumber: user.phone_number || '',
    password: '',
    confirmPassword: '',
    departmentId: user.department_id || null,
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = false // 默认不显示密码字段
  userManagement.modalVisible = true
}

// 处理用户表单提交
const handleUserFormSubmit = async () => {
  try {
    // 简单验证
    userManagement.form.usernameError = getUsernameError(userManagement.form.username.trim())
    if (userManagement.form.usernameError) {
      message.error(userManagement.form.usernameError)
      usernameInputRef.value?.focus()
      return
    }

    // 验证手机号
    if (userManagement.form.phoneNumber && !validatePhoneNumber(userManagement.form.phoneNumber)) {
      message.error('请输入正确的手机号格式')
      return
    }

    if (userManagement.displayPasswordFields) {
      if (!userManagement.form.password) {
        message.error('密码不能为空')
        return
      }

      if (!isPasswordLongEnough(userManagement.form.password)) {
        message.error(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`)
        return
      }

      if (userManagement.form.password !== userManagement.form.confirmPassword) {
        message.error('两次输入的密码不一致')
        return
      }
    }

    userManagement.loading = true

    // 根据模式决定创建还是更新用户
    if (userManagement.editMode) {
      // 创建更新数据对象
      const updateData = {
        username: userManagement.form.username.trim()
      }

      // 添加手机号字段
      if (userManagement.form.phoneNumber) {
        updateData.phone_number = userManagement.form.phoneNumber
      }

      if (userStore.isSuperAdmin) updateData.role = userManagement.form.role

      // 超级管理员可以修改部门
      if (userStore.isSuperAdmin && userManagement.form.departmentId) {
        updateData.department_id = userManagement.form.departmentId
      }

      // 如果显示了密码字段并且填写了密码，才更新密码
      if (userManagement.displayPasswordFields && userManagement.form.password) {
        updateData.password = userManagement.form.password
      }

      await userStore.updateUser(userManagement.editUserId, updateData)
      message.success('用户更新成功')
    } else {
      // 创建新用户
      const createData = {
        username: userManagement.form.username.trim(),
        password: userManagement.form.password,
        role: userManagement.form.role
      }

      // 超级管理员可以指定部门
      if (userStore.isSuperAdmin && userManagement.form.departmentId) {
        createData.department_id = userManagement.form.departmentId
      }

      // 添加手机号字段（如果填写了）
      if (userManagement.form.phoneNumber) {
        createData.phone_number = userManagement.form.phoneNumber
      }

      await userStore.createUser(createData)
      message.success('用户创建成功')
    }

    // 重新获取用户列表
    await fetchUsers()
    userManagement.modalVisible = false
  } catch (error) {
    console.error('用户操作失败:', error)
    message.error(error.message || '操作失败，请稍后重试')
  } finally {
    userManagement.loading = false
  }
}

/** 删除各页选中的用户，保留删除结果未确认的记录以便核查。 */
function deleteSelectedUsers() {
  confirmManagementBatchDeletion({
    ids: [...selectedUserIds.value],
    label: '用户',
    consequence: '',
    deleteById: userStore.deleteUser,
    async refresh(failedIds) {
      selectedUserIds.value = failedIds
      await fetchUsers()
    }
  })
}

// 删除用户
const confirmDeleteUser = (user) => {
  // 自己不能删除自己
  if (user.id === userStore.userId) {
    message.error('不能删除自己的账户')
    return
  }

  // 确认对话框
  Modal.confirm({
    title: '确认删除用户',
    content: `确定要删除用户 "${user.username}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.deleteUser(user.id)
        selectedUserIds.value = selectedUserIds.value.filter(id => id !== user.id)
        message.success('用户删除成功')
        // 重新获取用户列表
        await fetchUsers()
      } catch (error) {
        console.error('删除用户失败:', error)
        message.error(error.message || '删除失败，请稍后重试')
      } finally {
        userManagement.loading = false
      }
    }
  })
}

// 在组件挂载时获取用户列表
onMounted(async () => {
  await fetchUsers()
  await fetchDepartments()
})

onUnmounted(() => {
  if (filterRequestTimer) clearTimeout(filterRequestTimer)
  latestUserRequest += 1
})
</script>

<style lang="less" scoped>
.user-identity-details {
  max-width: min(320px, 80vw);
  overflow-wrap: anywhere;
}

.user-management {
  .user-table-toolbar,
  .user-table-filters,
  .user-selection-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }

  .user-table-toolbar {
    gap: 8px 16px;
    margin-bottom: 12px;
  }

  .user-table-filters {
    flex: 1 1 398px;
    min-width: 0;

    .search-input {
      width: 240px;
      max-width: 100%;

      :deep(.ant-input-prefix) {
        color: var(--gray-500);
        margin-right: 6px;
      }
    }

    .user-kind-select {
      width: 150px;
      max-width: 100%;
    }

    .filter-label {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }
  }

  .user-selection-actions {
    margin-inline-start: auto;
    justify-content: flex-end;
  }

  .user-table-cell {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    width: 100%;

    .user-avatar {
      flex-shrink: 0;
    }

    .user-meta {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1;
      padding: 0;
      border: 0;
      background: transparent;
      text-align: left;
      cursor: pointer;

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 2px;
        border-radius: 4px;
      }

      .user-name {
        font-weight: 500;
        color: var(--gray-900);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 13px;
        line-height: 18px;
      }

      .user-uid {
        font-size: 12px;
        color: var(--gray-600);
        line-height: 14px;
        font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }
  }

  .role-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
    line-height: 16px;
    background: var(--gray-100);
    color: var(--gray-600);
    white-space: nowrap;

    svg {
      flex-shrink: 0;
    }

    &.superadmin {
      background: var(--color-warning-50);
      color: var(--color-warning-900);
    }

    &.admin {
      background: var(--main-30);
      color: var(--main-color);
    }
  }

  .header-filter-cell {
    display: flex;
    align-items: center;
    gap: 2px;

    > span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .header-filter-btn {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 5px;
    background: transparent;
    color: var(--gray-400);
    cursor: pointer;

    &:hover {
      background: var(--gray-100);
      color: var(--gray-700);
    }

    &.active {
      color: var(--main-color);
    }

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: -2px;
    }
  }

  .dept-text,
  .time-text {
    color: var(--gray-600);
    font-size: 12px;
  }

  .dept-text {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .phone-text {
    font-size: 12px;
    color: var(--gray-700);
    font-family: 'JetBrains Mono', 'Fira Code', 'Menlo', monospace;
  }

  .pagination-section {
    display: flex;
    justify-content: flex-end;
    margin-top: 16px;
  }
}

.user-modal {
  .user-form {
    .password-toggle {
      margin-bottom: 16px;
      padding: 12px 16px;
      background: var(--gray-25);
      border-radius: 8px;
      border: 1px solid var(--gray-100);

      :deep(.ant-checkbox-wrapper) {
        font-weight: 500;
        color: var(--gray-700);
        font-size: 13px;
      }
    }
  }
}
</style>

<style lang="less">
// 表头筛选菜单挂载到 body，脱离组件 scoped 祖先链，样式需要全局提供。
.yuxi-header-filter-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 224px;
  max-width: calc(100vw - 48px);
  padding: 8px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: var(--shadow-popover);

  &.department-filter-panel {
    width: 280px;
  }

  .header-filter-options {
    display: flex;
    flex-direction: column;
    flex-wrap: nowrap;
    align-self: stretch;
    gap: 2px;
    max-height: 264px;
    overflow-y: auto;
    scrollbar-width: thin;

    .ant-checkbox-wrapper {
      display: flex;
      align-items: flex-start;
      min-width: 0;
      margin-inline-start: 0;
      padding: 3px 4px;
      border-radius: 6px;
      font-size: 13px;
      color: var(--gray-700);
      white-space: normal;

      &:hover {
        background: var(--gray-50);
      }

      .ant-checkbox + span {
        min-width: 0;
        overflow-wrap: anywhere;
      }
    }
  }

  .yuxi-header-filter-search {
    flex-shrink: 0;
  }

  .header-filter-empty {
    padding: 4px 0;
    color: var(--gray-500);
    font-size: 12px;
  }

  .header-filter-clear {
    margin-right: auto;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--main-color);
    font-size: 12px;
    cursor: pointer;

    &:hover {
      color: var(--main-600);
    }

    &:disabled {
      color: var(--gray-400);
      cursor: not-allowed;
    }

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }
  }

  .header-filter-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--gray-100);
  }
}
</style>
