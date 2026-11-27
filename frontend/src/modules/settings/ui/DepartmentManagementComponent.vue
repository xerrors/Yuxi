<template>
  <div class="department-management settings-page">
    <!-- 头部区域 -->
    <div class="settings-header">
      <div class="settings-header-copy">
        <div class="settings-section-title">部门管理</div>
        <p class="settings-section-description">管理系统部门，部门下的用户会被隔离管理。</p>
      </div>
      <div class="settings-header-actions">
        <a-button
          @click="handleRefresh"
          :loading="departmentManagement.refreshing"
          title="刷新"
          class="yuxi-refresh-button lucide-icon-btn"
        >
          <template #icon
            ><RefreshCw :size="16" :class="{ 'yuxi-rotate': departmentManagement.refreshing }"
          /></template>
        </a-button>
        <a-button
          v-if="selectedDepartments.length"
          danger
          class="lucide-icon-btn"
          :disabled="!canSelectDepartments"
          @click="deleteSelectedDepartments"
        >
          <Trash2 :size="14" />
          批量删除 ({{ selectedDepartments.length }})
        </a-button>
        <a-button type="primary" @click="showAddDepartmentModal" class="lucide-icon-btn">
          <template #icon><Plus :size="16" /></template>
          添加部门
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="settings-management-content">
      <a-spin :spinning="departmentManagement.loading">
        <div v-if="departmentManagement.error" class="settings-error">
          <a-alert type="error" :message="departmentManagement.error" show-icon />
        </div>

        <template v-if="departmentManagement.departments.length > 0">
          <div class="settings-table-wrapper">
            <a-table
              :dataSource="departmentManagement.departments"
              :columns="columns"
              :rowKey="(record) => record.id"
              :row-selection="departmentSelection"
              :pagination="false"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'name'">
                  <div class="settings-table-title">
                    <Building2 :size="15" class="settings-table-icon" />
                    <span class="settings-table-name" :title="record.name">{{ record.name }}</span>
                  </div>
                </template>
                <template v-if="column.key === 'description'">
                  <span class="desc-text">{{ record.description || '-' }}</span>
                </template>
                <template v-if="column.key === 'userCount'">
                  <span class="count-badge">{{ record.user_count ?? 0 }} 人</span>
                </template>
                <template v-if="column.key === 'action'">
                  <a-space :size="4">
                    <a-tooltip title="编辑部门">
                      <a-button
                        type="text"
                        size="small"
                        class="yuxi-table-action lucide-icon-btn"
                        @click="showEditDepartmentModal(record)"
                      >
                        <SquarePen :size="14" />
                      </a-button>
                    </a-tooltip>
                    <a-tooltip title="删除部门">
                      <a-button
                        type="text"
                        size="small"
                        danger
                        :disabled="isDepartmentDeleteDisabled(record)"
                        class="yuxi-table-action lucide-icon-btn"
                        @click="confirmDeleteDepartment(record)"
                      >
                        <Trash2 :size="14" />
                      </a-button>
                    </a-tooltip>
                  </a-space>
                </template>
              </template>
            </a-table>
          </div>
        </template>

        <div v-else class="settings-empty">
          <a-empty description="暂无部门数据" />
        </div>
      </a-spin>
    </div>

    <!-- 部门表单模态框 -->
    <a-modal
      v-model:open="departmentManagement.modalVisible"
      :title="departmentManagement.modalTitle"
      @ok="handleDepartmentFormSubmit"
      :confirmLoading="departmentManagement.loading"
      @cancel="departmentManagement.modalVisible = false"
      :maskClosable="false"
      width="520px"
    >
      <a-form layout="vertical" autocomplete="off">
        <a-form-item label="部门名称" required>
          <a-input
            v-model:value="departmentManagement.form.name"
            placeholder="请输入部门名称"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item label="部门描述">
          <a-textarea
            v-model:value="departmentManagement.form.description"
            placeholder="请输入部门描述（可选）"
            :rows="3"
            :maxlength="255"
            show-count
          />
        </a-form-item>

        <a-divider v-if="!departmentManagement.editMode" />

        <template v-if="!departmentManagement.editMode">
          <p class="admin-section-hint">
            创建部门时必须同时创建管理员，该管理员将负责管理本部门用户
          </p>

          <a-form-item label="管理员UID" required>
            <a-input
              v-model:value="departmentManagement.form.adminUid"
              placeholder="请输入管理员UID（3-20位字母/数字/下划线）"
              size="large"
              :maxlength="20"
              name="new-department-admin-uid"
              autocomplete="off"
              @blur="checkAdminUid"
            />
            <div v-if="departmentManagement.form.uidError" class="yuxi-form-error">
              {{ departmentManagement.form.uidError }}
            </div>
            <div v-else class="yuxi-form-help">此 UID 将用于登录</div>
          </a-form-item>

          <a-form-item label="密码" required>
            <a-input-password
              v-model:value="departmentManagement.form.adminPassword"
              :placeholder="`请输入管理员密码（至少 ${MIN_PASSWORD_LENGTH} 位）`"
              size="large"
              :minlength="MIN_PASSWORD_LENGTH"
              :maxlength="50"
              name="new-department-admin-password"
              autocomplete="new-password"
            />
          </a-form-item>

          <a-form-item label="确认密码" required>
            <a-input-password
              v-model:value="departmentManagement.form.adminConfirmPassword"
              placeholder="请再次输入密码"
              size="large"
              :maxlength="50"
              name="new-department-admin-password-confirmation"
              autocomplete="new-password"
            />
          </a-form-item>

          <a-form-item label="手机号（可选）">
            <a-input
              v-model:value="departmentManagement.form.adminPhone"
              placeholder="请输入手机号（可用于登录）"
              size="large"
              :maxlength="11"
              name="new-department-admin-phone"
              autocomplete="off"
            />
            <div v-if="departmentManagement.form.phoneError" class="yuxi-form-error">
              {{ departmentManagement.form.phoneError }}
            </div>
          </a-form-item>
        </template>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted, watch } from 'vue'
import { notification, message, Modal } from 'ant-design-vue'
import { authApi, departmentApi } from '@/apis'
import { Building2, Plus, RefreshCw, SquarePen, Trash2 } from '@lucide/vue'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '@/modules/identity'
import { confirmManagementBatchDeletion } from '../model/confirmManagementBatchDeletion'

// 表格列定义
const columns = [
  {
    title: '部门名称',
    dataIndex: 'name',
    key: 'name',
    width: '28%'
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    width: '42%'
  },
  {
    title: '用户数量',
    dataIndex: 'user_count',
    key: 'userCount',
    width: '16%'
  },
  {
    title: '操作',
    key: 'action',
    width: '14%',
    align: 'center'
  }
]

// 部门管理状态
const departmentManagement = reactive({
  loading: false,
  refreshing: false,
  departments: [],
  error: null,
  modalVisible: false,
  modalTitle: '添加部门',
  editMode: false,
  editDepartmentId: null,
  form: {
    name: '',
    description: '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
})

/** 默认部门接收被删除部门的成员，始终禁止删除。 */
const isDepartmentDeleteDisabled = department => department.id === 1

const selectedDepartmentIds = ref([])
const canSelectDepartments = computed(() => !departmentManagement.loading && !departmentManagement.error)
const selectedDepartments = computed(() =>
  departmentManagement.departments.filter(department =>
    selectedDepartmentIds.value.includes(department.id) && !isDepartmentDeleteDisabled(department)
  )
)
const departmentSelection = computed(() => ({
  columnWidth: 44,
  selectedRowKeys: selectedDepartments.value.map(department => department.id),
  onChange: (ids) => { selectedDepartmentIds.value = ids },
  getCheckboxProps: (department) => ({
    disabled: !canSelectDepartments.value || isDepartmentDeleteDisabled(department),
    'aria-label': `选择部门 ${department.name}`
  })
}))

// 获取部门列表
const fetchDepartments = async () => {
  try {
    departmentManagement.loading = true
    departmentManagement.error = null
    const departments = await departmentApi.getDepartments()
    departmentManagement.departments = departments
  } catch (error) {
    console.error('获取部门列表失败:', error)
    departmentManagement.error = '获取部门列表失败'
  } finally {
    departmentManagement.loading = false
  }
}

// 刷新部门列表
const handleRefresh = async () => {
  if (departmentManagement.refreshing) return
  departmentManagement.refreshing = true
  try {
    await fetchDepartments()
    if (!departmentManagement.error) message.success('刷新成功')
  } catch (error) {
    console.error('刷新失败:', error)
    message.error('刷新失败')
  } finally {
    departmentManagement.refreshing = false
  }
}

// 打开添加部门模态框
const showAddDepartmentModal = () => {
  departmentManagement.modalTitle = '添加部门'
  departmentManagement.editMode = false
  departmentManagement.editDepartmentId = null
  departmentManagement.form = {
    name: '',
    description: '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
  departmentManagement.modalVisible = true
}

// 打开编辑部门模态框
const showEditDepartmentModal = (department) => {
  departmentManagement.modalTitle = '编辑部门'
  departmentManagement.editMode = true
  departmentManagement.editDepartmentId = department.id
  departmentManagement.form = {
    name: department.name,
    description: department.description || '',
    adminUid: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    uidError: '',
    phoneError: ''
  }
  departmentManagement.modalVisible = true
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听手机号输入变化
watch(
  () => departmentManagement.form.adminPhone,
  (newPhone) => {
    departmentManagement.form.phoneError = ''
    if (newPhone && !validatePhoneNumber(newPhone)) {
      departmentManagement.form.phoneError = '请输入正确的手机号格式'
    }
  }
)

// 检查管理员UID是否可用
const checkAdminUid = async () => {
  const uid = departmentManagement.form.adminUid.trim()
  departmentManagement.form.uidError = ''

  if (!uid) {
    return
  }

  // 验证格式
  if (!/^[a-zA-Z0-9_]+$/.test(uid)) {
    departmentManagement.form.uidError = 'UID只能包含字母、数字和下划线'
    return
  }

  if (uid.length < 3 || uid.length > 20) {
    departmentManagement.form.uidError = 'UID长度必须在3-20个字符之间'
    return
  }

  // 检查是否已存在
  try {
    const result = await authApi.checkUid(uid)
    if (!result.is_available) {
      departmentManagement.form.uidError = '该UID已被使用'
    }
  } catch (error) {
    console.error('检查UID失败:', error)
  }
}

// 处理部门表单提交
const handleDepartmentFormSubmit = async () => {
  try {
    // 验证部门名称
    if (!departmentManagement.form.name.trim()) {
      notification.error({ message: '部门名称不能为空' })
      return
    }

    if (departmentManagement.form.name.trim().length < 2) {
      notification.error({ message: '部门名称至少2个字符' })
      return
    }

    // 验证管理员UID
    const adminUid = departmentManagement.form.adminUid.trim()
    if (!adminUid) {
      notification.error({ message: '请输入管理员UID' })
      return
    }

    if (!/^[a-zA-Z0-9_]+$/.test(adminUid)) {
      notification.error({ message: 'UID只能包含字母、数字和下划线' })
      return
    }

    if (adminUid.length < 3 || adminUid.length > 20) {
      notification.error({ message: 'UID长度必须在3-20个字符之间' })
      return
    }

    if (departmentManagement.form.uidError) {
      notification.error({ message: '管理员UID已存在或格式错误' })
      return
    }

    // 验证密码
    if (!departmentManagement.form.adminPassword) {
      notification.error({ message: '请输入管理员密码' })
      return
    }

    if (!isPasswordLongEnough(departmentManagement.form.adminPassword)) {
      notification.error({ message: `密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符` })
      return
    }

    if (
      departmentManagement.form.adminPassword !== departmentManagement.form.adminConfirmPassword
    ) {
      notification.error({ message: '两次输入的密码不一致' })
      return
    }

    // 验证手机号
    if (
      departmentManagement.form.adminPhone &&
      !validatePhoneNumber(departmentManagement.form.adminPhone)
    ) {
      notification.error({ message: '请输入正确的手机号格式' })
      return
    }

    departmentManagement.loading = true

    if (departmentManagement.editMode) {
      // 更新部门
      await departmentApi.updateDepartment(departmentManagement.editDepartmentId, {
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined
      })
      notification.success({ message: '部门更新成功' })
    } else {
      // 创建部门，同时创建管理员
      await departmentApi.createDepartment({
        name: departmentManagement.form.name.trim(),
        description: departmentManagement.form.description.trim() || undefined,
        admin_uid: adminUid,
        admin_password: departmentManagement.form.adminPassword,
        admin_phone: departmentManagement.form.adminPhone || undefined
      })

      message.success(`部门创建成功，管理员 "${adminUid}" 已创建`)
    }

    // 重新获取部门列表
    await fetchDepartments()
    departmentManagement.modalVisible = false
  } catch (error) {
    console.error('部门操作失败:', error)
    notification.error({
      message: '操作失败',
      description: error.message || '请稍后重试'
    })
  } finally {
    departmentManagement.loading = false
  }
}

/** 批量确认时说明部门删除会迁移成员并清理部门资源。 */
function deleteSelectedDepartments() {
  if (!canSelectDepartments.value) return
  confirmManagementBatchDeletion({
    ids: selectedDepartments.value.map(department => department.id),
    label: '部门',
    consequence: '这些部门的成员会迁移到默认部门，部门 API Key 会被吊销。',
    deleteById: departmentApi.deleteDepartment,
    async refresh(failedIds) {
      selectedDepartmentIds.value = failedIds
      await fetchDepartments()
    }
  })
}

// 删除部门
const confirmDeleteDepartment = (department) => {
  Modal.confirm({
    title: '确认删除部门',
    content: `确定要删除部门 "${department.name}" 吗？此操作不可撤销。该部门下的用户会被迁移到默认部门，部门级配置和部门 API Key 会一并清理。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        departmentManagement.loading = true
        await departmentApi.deleteDepartment(department.id)
        message.success('部门删除成功')
        // 重新获取部门列表
        await fetchDepartments()
      } catch (error) {
        console.error('删除部门失败:', error)
        message.error(`删除失败，${error.message || '请稍后重试'}`)
      } finally {
        departmentManagement.loading = false
      }
    }
  })
}

// 在组件挂载时获取部门列表
onMounted(() => {
  fetchDepartments()
})
</script>

<style lang="less" scoped>
.department-management {
  .desc-text {
    color: var(--gray-500);
    font-size: 12px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.4;
  }

  .count-badge {
    display: inline-flex;
    align-items: center;
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--gray-100);
    color: var(--gray-600);
    font-size: 12px;
    font-weight: 500;
  }
}

</style>
