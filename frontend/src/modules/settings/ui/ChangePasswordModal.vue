<template>
  <a-modal
    :open="open"
    title="修改密码"
    ok-text="保存密码"
    cancel-text="取消"
    :confirm-loading="saving"
    :cancel-button-props="{ disabled: saving }"
    :closable="!saving"
    :keyboard="!saving"
    :mask-closable="false"
    :width="420"
    destroy-on-close
    @ok="submitPassword"
    @cancel="emit('update:open', false)"
  >
    <a-form ref="formRef" :model="passwords" layout="vertical" :disabled="saving">
      <a-form-item
        label="原密码"
        name="current_password"
        :rules="[{ required: true, message: '请输入原密码' }]"
      >
        <a-input-password v-model:value="passwords.current_password" autocomplete="current-password" />
      </a-form-item>
      <a-form-item
        label="新密码"
        name="new_password"
        :rules="[{ required: true, min: 8, message: '新密码至少需要 8 个字符' }]"
        extra="至少 8 个字符"
      >
        <a-input-password v-model:value="passwords.new_password" autocomplete="new-password" />
      </a-form-item>
      <a-form-item
        label="确认新密码"
        name="confirmation"
        :rules="[{ validator: validateConfirmation }]"
      >
        <a-input-password
          v-model:value="passwords.confirmation"
          autocomplete="new-password"
          @press-enter="submitPassword"
        />
      </a-form-item>
      <a-alert v-if="error" :message="error" type="error" show-icon role="alert" />
    </a-form>
  </a-modal>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { authApi } from '@/apis/auth_api'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['update:open'])
const formRef = ref(null)
const saving = ref(false)
const error = ref('')
const passwords = reactive({ current_password: '', new_password: '', confirmation: '' })

/** 确认密码仅用于前端核对，不发送到后端。 */
async function validateConfirmation(_rule, value) {
  if (!value) throw new Error('请再次输入新密码')
  if (value !== passwords.new_password) throw new Error('两次输入的新密码不一致')
}

/** 校验表单并提交本人密码修改，失败时保留输入便于修正。 */
async function submitPassword() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    await authApi.changePassword({
      current_password: passwords.current_password,
      new_password: passwords.new_password
    })
    message.success('密码修改成功')
    emit('update:open', false)
  } catch (err) {
    error.value = err.message || '修改密码失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

watch(() => props.open, () => {
  passwords.current_password = ''
  passwords.new_password = ''
  passwords.confirmation = ''
  error.value = ''
  formRef.value?.clearValidate()
})
</script>
