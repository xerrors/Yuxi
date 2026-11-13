<template>
  <div class="account-settings settings-page">
    <div class="settings-header">
      <div class="settings-header-copy">
        <div class="settings-section-title">账户设置</div>
        <p class="settings-section-description">管理当前账户资料、身份信息。</p>
      </div>
      <div class="settings-header-actions">
        <a-button
          class="yuxi-refresh-button"
          :loading="refreshing"
          aria-label="刷新账户资料"
          title="刷新"
          @click="refreshProfile"
        >
          <template #icon><RefreshCw :size="16" :class="{ 'yuxi-rotate': refreshing }" /></template>
        </a-button>
        <a-dropdown :trigger="['click']" placement="bottomRight">
          <a-button aria-label="更多账户操作" title="更多账户操作">
            <template #icon><Ellipsis :size="16" /></template>
          </a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item
                key="password"
                :disabled="userStore.isEnterpriseAccount"
                :title="userStore.isEnterpriseAccount ? '企业账号请在统一身份服务中修改密码' : undefined"
                @click="passwordModalOpen = true"
              >
                <template #icon><KeyRound :size="14" /></template>
                修改密码
              </a-menu-item>
              <a-menu-item key="copy" :disabled="!userStore.uid" @click="copyIdentifier">
                <template #icon><Copy :size="14" /></template>
                复制用户 ID
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </div>
    </div>

    <div class="settings-surface-card profile-card">
      <div class="profile-summary">
        <div class="profile-left">
          <a-upload
            :show-upload-list="false"
            :before-upload="beforeUpload"
            @change="handleAvatarChange"
            accept="image/*"
          >
            <div class="avatar-upload" :class="{ uploading: avatarUploading }">
              <FallbackAvatar
                :src="userStore.avatar"
                :name="userStore.displayName"
                :seed="userStore.uid || userStore.username"
                kind="user"
                :size="80"
                shape="circle"
                :alt="userStore.displayName"
                class="account-avatar"
              />
              <div class="avatar-mask">
                <Upload v-if="!avatarUploading" :size="16" />
                <RefreshCw v-else :size="16" class="yuxi-rotate" />
                <span>{{ userStore.avatar ? '更换' : '上传' }}</span>
              </div>
            </div>
          </a-upload>

          <div class="profile-fields">
            <div class="profile-row editable-row">
              <span class="profile-label">用户名</span>
              <a-input
                v-if="editingField === 'username'"
                ref="usernameInput"
                v-model:value="profileDraft.username"
                class="inline-input"
                size="small"
                :max-length="20"
                :disabled="savingField === 'username'"
                @press-enter="saveField('username')"
                @keydown.esc.stop.prevent="cancelField"
                @blur="cancelField"
              />
              <button
                v-else
                type="button"
                class="editable-value"
                @click="startFieldEdit('username')"
              >
                {{ userStore.displayName || '未设置' }}
              </button>
            </div>
            <div class="profile-row editable-row">
              <span class="profile-label">手机号</span>
              <a-input
                v-if="editingField === 'phone_number'"
                ref="phoneInput"
                v-model:value="profileDraft.phone_number"
                class="inline-input"
                size="small"
                :max-length="11"
                :disabled="savingField === 'phone_number'"
                @press-enter="saveField('phone_number')"
                @keydown.esc.stop.prevent="cancelField"
                @blur="cancelField"
              />
              <button
                v-else
                type="button"
                class="editable-value"
                @click="startFieldEdit('phone_number')"
              >
                {{ userStore.phoneNumber || '未设置' }}
              </button>
            </div>
            <div class="profile-row">
              <span class="profile-label">账号 ID</span>
              <div class="identifier-field">
                <div class="identifier-actions">
                  <span class="profile-value" :class="{ mono: !userStore.isEnterpriseAccount }">
                    {{ userStore.isEnterpriseAccount ? '企业账号' : userStore.uid || '未设置' }}
                  </span>
                  <a-button
                    v-if="userStore.isEnterpriseAccount"
                    type="text"
                    size="small"
                    :aria-expanded="identifierVisible"
                    aria-controls="account-identifier"
                    @click="identifierVisible = !identifierVisible"
                  >
                    <template #icon
                      ><ChevronUp v-if="identifierVisible" :size="14" /><ChevronDown
                        v-else
                        :size="14"
                    /></template>
                    {{ identifierVisible ? '收起' : '查看 ID' }}
                  </a-button>
                </div>
                <code
                  v-if="userStore.isEnterpriseAccount && identifierVisible"
                  id="account-identifier"
                  class="identifier-details mono"
                  >{{ userStore.uid }}</code
                >
              </div>
            </div>
          </div>
        </div>

        <div class="identity-panel">
          <div class="identity-item">
            <span class="identity-icon"><ShieldCheck :size="15" /></span>
            <span class="profile-label">权限</span>
            <span class="profile-value" :style="{ color: getRoleColor(userStore.userRole) }">
              {{ userRoleText }}
            </span>
          </div>
          <div class="identity-item">
            <span class="identity-icon"><Building2 :size="15" /></span>
            <span class="profile-label">部门</span>
            <span class="profile-value">{{ userStore.departmentName || '默认部门' }}</span>
          </div>
        </div>
      </div>
      <UserConfigSettingsCard ref="userConfigRef" />
    </div>

    <div class="settings-surface-card activity-card">
      <div class="settings-header">
        <div class="settings-header-copy">
          <div class="settings-section-title">消息活动</div>
          <p class="settings-section-description">近一年的消息数，包含用户消息、模型消息与工具消息。</p>
        </div>
      </div>
      <DashboardActivityHeatmap
        :data="activityData"
        :loading="activityLoading"
        value-key="message_count"
        value-label="条消息"
        total-unit="条消息"
        aria-label="近一年消息活动"
        empty-text="暂无消息记录"
      />
    </div>
    <ChangePasswordModal v-model:open="passwordModalOpen" />
  </div>
</template>

<script setup>
import UserConfigSettingsCard from '@/modules/settings/ui/UserConfigSettingsCard.vue'
import ChangePasswordModal from '@/modules/settings/ui/ChangePasswordModal.vue'
import DashboardActivityHeatmap from '@/modules/dashboard/ui/DashboardActivityHeatmap.vue'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  Building2,
  ChevronDown,
  ChevronUp,
  Copy,
  Ellipsis,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  Upload
} from '@lucide/vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import { useUserStore } from '@/modules/identity'
import { userApi } from '@/apis/user_api'
import { copyTextToClipboard } from '@/shared/lib/clipboard'

const userStore = useUserStore()
const avatarUploading = ref(false)
const refreshing = ref(false)
const savingField = ref('')
const editingField = ref('')
const identifierVisible = ref(false)
const passwordModalOpen = ref(false)
const usernameInput = ref(null)
const phoneInput = ref(null)
const userConfigRef = ref(null)
const activityData = ref([])
const activityLoading = ref(false)
const profileDraft = reactive({
  username: '',
  phone_number: ''
})

const userRoleText = computed(() => {
  switch (userStore.userRole) {
    case 'superadmin':
      return '超级管理员'
    case 'admin':
      return '管理员'
    case 'user':
      return '普通用户'
    default:
      return '未知角色'
  }
})

const syncProfileDraft = () => {
  profileDraft.username = userStore.displayName || ''
  profileDraft.phone_number = userStore.phoneNumber || ''
}

const loadMessageActivity = async () => {
  activityLoading.value = true
  try {
    const res = await userApi.getMessageActivity()
    activityData.value = res.daily_message_counts || []
  } catch (error) {
    console.error('加载消息活动失败:', error)
    message.error('加载消息活动失败：' + (error.message || '请稍后重试'))
  } finally {
    activityLoading.value = false
  }
}

const refreshProfile = async () => {
  refreshing.value = true
  try {
    await Promise.all([
      userStore.getCurrentUser(),
      userConfigRef.value?.refresh?.(),
      loadMessageActivity()
    ])
    syncProfileDraft()
    message.success('账户设置已刷新')
  } catch (error) {
    console.error('刷新用户信息失败:', error)
    message.error('刷新失败：' + (error.message || '请稍后重试'))
  } finally {
    refreshing.value = false
  }
}

const startFieldEdit = async (field) => {
  syncProfileDraft()
  editingField.value = field
  await nextTick()
  const inputRef = field === 'username' ? usernameInput.value : phoneInput.value
  inputRef?.focus?.()
}

const cancelField = () => {
  if (savingField.value) return
  editingField.value = ''
  syncProfileDraft()
}

const saveField = async (field) => {
  const payload = {}
  if (field === 'username') {
    const username = profileDraft.username.trim()
    if (username === userStore.displayName) {
      cancelField()
      return
    }
    if (username.length < 2 || username.length > 20) {
      message.error('用户名长度必须在 2-20 个字符之间')
      return
    }
    payload.username = username
  }

  if (field === 'phone_number') {
    const phoneNumber = profileDraft.phone_number.trim()
    if (phoneNumber && !validatePhoneNumber(phoneNumber)) {
      message.error('请输入正确的手机号格式')
      return
    }
    if (phoneNumber === (userStore.phoneNumber || '')) {
      cancelField()
      return
    }
    payload.phone_number = phoneNumber
  }

  savingField.value = field
  try {
    await userStore.updateProfile(payload)
    syncProfileDraft()
    editingField.value = ''
    message.success('个人资料更新成功')
  } catch (error) {
    console.error('更新个人资料失败:', error)
    message.error('更新失败：' + (error.message || '请稍后重试'))
  } finally {
    savingField.value = ''
  }
}

/** 复制原始账号标识，HTTP 环境复用项目的剪贴板实现。 */
const copyIdentifier = async () => {
  try {
    await copyTextToClipboard(userStore.uid)
    message.success('账号 ID 已复制')
  } catch {
    message.error('复制失败，请查看 ID 后手动复制')
  }
}

const getRoleColor = (role) => {
  switch (role) {
    case 'superadmin':
      return 'var(--gray-900)'
    case 'admin':
      return 'var(--color-primary-500)'
    case 'user':
      return 'var(--color-success-500)'
    default:
      return 'var(--gray-600)'
  }
}

const validatePhoneNumber = (phone) => {
  if (!phone) return true
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

const beforeUpload = (file) => {
  const isImage = file.type.startsWith('image/')
  if (!isImage) {
    message.error('只能上传图片文件！')
    return false
  }

  const isLt5M = file.size / 1024 / 1024 < 5
  if (!isLt5M) {
    message.error('图片大小不能超过 5MB！')
    return false
  }

  return true
}

const handleAvatarChange = async (info) => {
  if (info.file.status === 'uploading') {
    avatarUploading.value = true
    return
  }

  if (info.file.status === 'done') {
    avatarUploading.value = false
    return
  }

  try {
    avatarUploading.value = true
    await userStore.uploadAvatar(info.file.originFileObj || info.file)
    message.success('头像上传成功！')
  } catch (error) {
    console.error('头像上传失败:', error)
    message.error('头像上传失败：' + (error.message || '请稍后重试'))
  } finally {
    avatarUploading.value = false
  }
}

watch(
  () => userStore.uid,
  () => {
    identifierVisible.value = false
  }
)

watch(() => [userStore.username, userStore.phoneNumber], syncProfileDraft, { immediate: true })

onMounted(loadMessageActivity)
</script>

<style lang="less" scoped>
:global(:root.dark .account-settings .profile-card),
:global(:root.dark .account-settings .activity-card) {
  background: var(--gray-0);
}

.account-settings {
  .profile-card {
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: var(--gray-25);
  }

  .activity-card {
    margin-top: 16px;
    background: var(--gray-25);
  }

  .profile-summary {
    display: flex;
    align-items: stretch;
    justify-content: space-between;
    gap: 16px;

    @media (max-width: 760px) {
      flex-direction: column;
    }
  }

  .profile-left {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 16px;
    flex: 1;

    @media (max-width: 520px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .avatar-upload {
    width: 80px;
    height: 80px;
    position: relative;
    cursor: pointer;
    border-radius: 50%;
    overflow: hidden;
    flex: 0 0 auto;

    .account-avatar {
      width: 80px;
      height: 80px;
      border: 3px solid var(--gray-0);
    }

    &:hover .avatar-mask,
    &.uploading .avatar-mask {
      opacity: 1;
    }
  }

  .avatar-mask {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    color: var(--gray-0);
    font-size: 12px;
    background: rgba(0, 0, 0, 0.48);
    opacity: 0;
    transition: opacity 0.2s ease;
  }

  .profile-fields {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .profile-row {
    min-width: 0;
    display: grid;
    grid-template-columns: 48px minmax(0, 1fr);
    align-items: center;
    gap: 6px;
  }

  .profile-label {
    color: var(--gray-600);
    font-size: 13px;
    flex-shrink: 0;
  }

  .profile-value,
  .editable-value {
    min-width: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 24px;
    overflow: hidden;
    text-align: left;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .editable-value {
    width: fit-content;
    max-width: 100%;
    padding: 0 6px;
    margin-left: -6px;
    border: none;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;

    &:hover {
      color: var(--main-color);
      background: var(--main-5);
    }
  }

  .identifier-field {
    min-width: 0;
  }

  .identifier-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;

    .profile-value,
    :deep(.ant-btn:not(:disabled)) {
      color: var(--gray-700);
    }

    .profile-value {
      max-width: 100%;
      margin-right: 4px;
    }
  }

  .identifier-details {
    display: block;
    margin-top: 6px;
    padding: 8px 10px;
    border-radius: 6px;
    background: var(--gray-50);
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.6;
    overflow-wrap: anywhere;
    user-select: text;
  }

  .inline-input {
    width: min(260px, 100%);
  }

  .identity-panel {
    min-width: 220px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 8px;
    padding: 12px;
    border-radius: 10px;
    background: var(--gray-0);

    @media (max-width: 760px) {
      min-width: 0;
    }
  }

  .identity-item {
    min-width: 0;
    display: grid;
    grid-template-columns: 20px 32px minmax(0, 1fr);
    align-items: center;
    gap: 6px;
  }

  .identity-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 6px;
    background: var(--gray-50);
    color: var(--gray-500);
  }

  .mono {
    font-family: 'Monaco', 'Consolas', monospace;
  }

}

</style>
