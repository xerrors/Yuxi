<template>
  <div class="department-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">部门管理</div>
        <p class="section-description">管理系统部门，部门下的用户会被隔离管理。</p>
      </div>
      <a-button type="primary" @click="showAddDepartmentModal" class="add-btn lucide-icon-btn">
        <template #icon><Plus :size="16" /></template>
        添加部门
      </a-button>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="departmentManagement.loading">
        <div v-if="departmentManagement.error" class="error-message">
          <a-alert type="error" :message="departmentManagement.error" show-icon />
        </div>

        <template v-if="departmentManagement.departments.length > 0">
          <a-table
            :dataSource="departmentManagement.departments"
            :columns="columns"
            :rowKey="(record) => record.id"
            :pagination="false"
            class="department-table"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <div class="department-name">
                  <span class="name-text">{{ record.name }}</span>
                </div>
              </template>
              <template v-if="column.key === 'description'">
                <span class="description-text">{{ record.description || '-' }}</span>
              </template>
              <template v-if="column.key === 'userCount'">
                <span>{{ record.user_count ?? 0 }} 人</span>
              </template>
              <template v-if="column.key === 'action'">
                <a-space>
                  <a-tooltip title="编辑部门">
                    <a-button
                      type="text"
                      size="small"
                      @click="showEditDepartmentModal(record)"
                      class="action-btn lucide-icon-btn"
                    >
                      <Pencil :size="14" />
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="删除部门">
                    <a-button
                      type="text"
                      size="small"
                      danger
                      @click="confirmDeleteDepartment(record)"
                      :disabled="record.user_count > 0"
                      class="action-btn lucide-icon-btn"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </a-tooltip>
                </a-space>
              </template>
            </template>
          </a-table>
        </template>

        <div v-else class="empty-state">
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
      class="department-modal"
    >
      <a-form layout="vertical" class="department-form">
        <a-form-item label="部门名称" required class="form-item">
          <a-input
            v-model:value="departmentManagement.form.name"
            placeholder="请输入部门名称"
            size="large"
            :maxlength="50"
          />
        </a-form-item>

        <a-form-item label="部门描述" class="form-item">
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
          <div class="admin-section-title">
            <Users :size="16" />
            <span>部门管理员</span>
          </div>
          <p class="admin-section-hint">
            创建部门时必须同时创建管理员，该管理员将负责管理本部门用户
          </p>

          <a-form-item label="管理员用户ID" required class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminUserId"
              placeholder="请输入管理员用户ID（3-20位字母/数字/下划线）"
              size="large"
              :maxlength="20"
              @blur="checkAdminUserId"
            />
            <div v-if="departmentManagement.form.userIdError" class="error-text">
              {{ departmentManagement.form.userIdError }}
            </div>
            <div v-else class="help-text">此ID将用于登录</div>
          </a-form-item>

          <a-form-item label="密码" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminPassword"
              placeholder="请输入管理员密码"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item label="确认密码" required class="form-item">
            <a-input-password
              v-model:value="departmentManagement.form.adminConfirmPassword"
              placeholder="请再次输入密码"
              size="large"
              :maxlength="50"
            />
          </a-form-item>

          <a-form-item label="手机号（可选）" class="form-item">
            <a-input
              v-model:value="departmentManagement.form.adminPhone"
              placeholder="请输入手机号（可用于登录）"
              size="large"
              :maxlength="11"
            />
            <div v-if="departmentManagement.form.phoneError" class="error-text">
              {{ departmentManagement.form.phoneError }}
            </div>
          </a-form-item>
        </template>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, onMounted, watch } from 'vue'
import { notification, Modal } from 'ant-design-vue'
import { departmentApi, apiSuperAdminGet } from '@/apis'
import { Delete as Trash2, Edit3 as Pencil, Plus, Users } from 'lucide-vue-next'

// 表格列定义
const columns = [
  {
    title: '部门名称',
    dataIndex: 'name',
    key: 'name',
    width: 200
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true
  },
  {
    title: '用户数量',
    dataIndex: 'user_count',
    key: 'userCount',
    width: 100,
    align: 'center'
  },
  {
    title: '操作',
    key: 'action',
    width: 120,
    align: 'center'
  }
]

// 部门管理状态
const departmentManagement = reactive({
  loading: false,
  departments: [],
  error: null,
  modalVisible: false,
  modalTitle: '添加部门',
  editMode: false,
  editDepartmentId: null,
  form: {
    name: '',
    description: '',
    adminUserId: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    userIdError: '',
    phoneError: ''
  }
})

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

// 打开添加部门模态框
const showAddDepartmentModal = () => {
  departmentManagement.modalTitle = '添加部门'
  departmentManagement.editMode = false
  departmentManagement.editDepartmentId = null
  departmentManagement.form = {
    name: '',
    description: '',
    adminUserId: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    userIdError: '',
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
    adminUserId: '',
    adminPassword: '',
    adminConfirmPassword: '',
    adminPhone: '',
    userIdError: '',
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

// 检查管理员用户ID是否可用
const checkAdminUserId = async () => {
  const userId = departmentManagement.form.adminUserId.trim()
  departmentManagement.form.userIdError = ''

  if (!userId) {
    return
  }

  // 验证格式
  if (!/^[a-zA-Z0-9_]+$/.test(userId)) {
    departmentManagement.form.userIdError = '用户ID只能包含字母、数字和下划线'
    return
  }

  if (userId.length < 3 || userId.length > 20) {
    departmentManagement.form.userIdError = '用户ID长度必须在3-20个字符之间'
    return
  }

  // 检查是否已存在
  try {
    const result = await apiSuperAdminGet(`/api/auth/check-user-id/${userId}`)
    if (!result.is_available) {
      departmentManagement.form.userIdError = '该用户ID已被使用'
    }
  } catch (error) {
    console.error('检查用户ID失败:', error)
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

    // 验证管理员用户ID
    const adminUserId = departmentManagement.form.adminUserId.trim()
    if (!adminUserId) {
      notification.error({ message: '请输入管理员用户ID' })
      return
    }

    if (!/^[a-zA-Z0-9_]+$/.test(adminUserId)) {
      notification.error({ message: '用户ID只能包含字母、数字和下划线' })
      return
    }

    if (adminUserId.length < 3 || adminUserId.length > 20) {
      notification.error({ message: '用户ID长度必须在3-20个字符之间' })
      return
    }

    if (departmentManagement.form.userIdError) {
      notification.error({ message: '管理员用户ID已存在或格式错误' })
      return
    }

    // 验证密码
    if (!departmentManagement.form.adminPassword) {
      notification.error({ message: '请输入管理员密码' })
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
        admin_user_id: adminUserId,
        admin_password: departmentManagement.form.adminPassword,
        admin_phone: departmentManagement.form.adminPhone || undefined
      })

      notification.success({ message: `部门创建成功，管理员 "${adminUserId}" 已创建` })
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

// 删除部门
const confirmDeleteDepartment = (department) => {
  Modal.confirm({
    title: '确认删除部门',
    content: `确定要删除部门 "${department.name}" 吗？此操作不可撤销。部门下必须没有用户才能删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        departmentManagement.loading = true
        await departmentApi.deleteDepartment(department.id)
        notification.success({ message: '部门删除成功' })
        // 重新获取部门列表
        await fetchDepartments()
      } catch (error) {
        console.error('删除部门失败:', error)
        notification.error({
          message: '删除失败',
          description: error.message || '请稍后重试'
        })
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
  .content-section {
    overflow: hidden;

    .error-message {
      padding: 16px 24px;
    }

    .empty-state {
      padding: 60px 20px;
      text-align: center;
    }

    .department-table {
      :deep(.ant-table-thead > tr > th) {
        background: var(--gray-50);
        font-weight: 500;
        padding: 8px 12px;
      }

      :deep(.ant-table-tbody > tr > td) {
        padding: 8px 12px;
      }

      .department-name {
        .name-text {
          font-weight: 500;
          color: var(--gray-900);
        }
      }

      .description-text {
        color: var(--gray-600);
      }

      .action-btn {
        padding: 4px 8px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }
      }
    }
  }
}

.department-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-body) {
    padding: 24px;
  }

  .department-form {
    .form-item {
      margin-bottom: 20px;

      :deep(.ant-form-item-label) {
        padding-bottom: 4px;

        label {
          font-weight: 500;
          color: var(--gray-900);
        }
      }
    }
  }

  .error-text {
    color: var(--color-error-500);
    font-size: 12px;
    margin-top: 4px;
    line-height: 1.3;
  }

  .help-text {
    color: var(--gray-600);
    font-size: 12px;
    margin-top: 4px;
    line-height: 1.3;
  }
}
</style>
