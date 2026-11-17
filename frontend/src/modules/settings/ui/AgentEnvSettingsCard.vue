<template>
  <div class="agent-env-settings settings-page">
    <UserSandboxSettingsCard ref="sandboxSettings" />
    <a-spin :spinning="loading">
      <AgentEnvEditor
        :key="editorRevision"
        :modelValue="draftEnv"
        :locked-keys="savedEnvKeys"
        :key-verification="verificationByKey"
        conceal-locked-values
        @update:modelValue="updateDraftEnv"
      >
        <template #actions>
          <a-button
            class="lucide-icon-btn"
            type="text"
            :loading="loading"
            title="刷新环境变量"
            aria-label="刷新环境变量"
            @click="loadAgentEnv"
          >
            <template #icon><RefreshCw :size="16" /></template>
          </a-button>
        </template>
      </AgentEnvEditor>
    </a-spin>
    <div class="env-footer">
      <a-tooltip
        :trigger="['hover', 'focus']"
        title="账户级配置，保存后用于新运行；当前运行保持原配置。验证读取最新保存值，并自动创建或恢复环境，结果仅适用于本次命令进程。"
      >
        <a-button type="text" class="env-help lucide-icon-btn" aria-label="环境变量使用说明">
          <Info :size="14" aria-hidden="true" />
          生效说明
        </a-button>
      </a-tooltip>
      <div class="env-footer-actions">
        <a-button
          title="验证已保存的配置，将自动创建或恢复环境"
          :loading="verifying"
          :disabled="loading || saving || hasUnsavedChanges || !savedEnvKeys.length"
          @click="verifyAgentEnv"
          >验证配置</a-button
        >
        <a-button
          type="primary"
          :loading="saving"
          :disabled="loading || !hasUnsavedChanges"
          @click="saveAgentEnv"
          >保存更改</a-button
        >
      </div>
    </div>
    <a-alert
      v-if="verificationError"
      class="env-verification-error"
      type="error"
      :message="verificationError"
      show-icon
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Info, RefreshCw } from '@lucide/vue'
import { agentEnvApi } from '@/apis/agent_env_api'
import AgentEnvEditor from './AgentEnvEditor.vue'
import UserSandboxSettingsCard from './UserSandboxSettingsCard.vue'

const ENV_KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const MAX_ENV_COUNT = 200
const MAX_ENV_KEY_LENGTH = 128
const MAX_ENV_VALUE_LENGTH = 32768

const loading = ref(false)
const sandboxSettings = ref(null)
const saving = ref(false)
const verifying = ref(false)
const verification = ref(null)
const verificationError = ref('')
const savedUpdatedAt = ref(null)
let verificationRevision = 0
const draftEnv = ref({})
const lastSavedEnv = ref({})
const editorRevision = ref(0)

const normalizeEnv = (env) => {
  if (!env || typeof env !== 'object' || Array.isArray(env)) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(env)
      .map(([key, value]) => [key.trim(), value == null ? '' : String(value)])
      .filter(([key]) => key)
  )
}

const isSameEnv = (left, right) => {
  const leftEntries = Object.entries(left)
  const rightEntries = Object.entries(right)
  if (leftEntries.length !== rightEntries.length) return false
  return leftEntries.every(([key, value]) => right[key] === value)
}

const savedEnvKeys = computed(() => Object.keys(lastSavedEnv.value || {}))
const hasUnsavedChanges = computed(() => !isSameEnv(draftEnv.value, lastSavedEnv.value))
const verificationByKey = computed(
  () => new Map((verification.value?.variables || []).map(({ key, matches }) => [key, matches]))
)

function invalidateVerification() {
  verificationRevision += 1
  verification.value = null
  verificationError.value = ''
  return verificationRevision
}

const updateDraftEnv = (value) => {
  const nextEnv = normalizeEnv(value)
  if (isSameEnv(draftEnv.value, nextEnv)) return
  draftEnv.value = nextEnv
  invalidateVerification()
}

const validateEnv = (env) => {
  const entries = Object.entries(env)
  if (entries.length > MAX_ENV_COUNT) {
    message.error(`环境变量数量上限为 ${MAX_ENV_COUNT} 个`)
    return false
  }

  for (const [key, value] of entries) {
    if (key.length > MAX_ENV_KEY_LENGTH) {
      message.error(`变量名长度上限为 ${MAX_ENV_KEY_LENGTH} 个字符`)
      return false
    }
    if (!ENV_KEY_PATTERN.test(key)) {
      message.error(`变量名 ${key} 格式无效`)
      return false
    }
    if (value.length > MAX_ENV_VALUE_LENGTH) {
      message.error(`变量 ${key} 的值超过长度上限`)
      return false
    }
  }
  return true
}

const loadAgentEnv = async () => {
  invalidateVerification()
  loading.value = true
  try {
    const res = await agentEnvApi.get()
    const env = normalizeEnv(res.env)
    draftEnv.value = env
    lastSavedEnv.value = env
    savedUpdatedAt.value = res.updated_at
    editorRevision.value += 1
  } catch (error) {
    message.error(error.message || '环境变量读取失败')
  } finally {
    loading.value = false
  }
}

const saveAgentEnv = async () => {
  const env = normalizeEnv(draftEnv.value)
  if (!validateEnv(env)) return
  if (isSameEnv(env, lastSavedEnv.value)) {
    message.info('配置无变更')
    return
  }

  saving.value = true
  invalidateVerification()
  try {
    const res = await agentEnvApi.update(env)
    draftEnv.value = env
    lastSavedEnv.value = env
    savedUpdatedAt.value = res.updated_at
    editorRevision.value += 1
    message.success('配置已保存，将在新启动的运行中生效')
  } catch (error) {
    message.error(error.message || '环境变量保存失败')
  } finally {
    saving.value = false
  }
}

const verifyAgentEnv = async () => {
  const revision = invalidateVerification()
  verifying.value = true
  try {
    const result = await agentEnvApi.verify()
    if (revision !== verificationRevision || hasUnsavedChanges.value) return
    if (result.updated_at !== savedUpdatedAt.value) {
      verificationError.value = '配置已更新，请刷新后重新验证。'
      return
    }
    verification.value = result
  } catch (error) {
    if (revision === verificationRevision && !hasUnsavedChanges.value) {
      verificationError.value = error.message || '生效验证失败，请检查环境状态'
    }
  } finally {
    verifying.value = false
    await sandboxSettings.value?.refresh()
  }
}

onMounted(loadAgentEnv)
</script>

<style lang="less" scoped>
.env-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--gray-150);
}
.env-footer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: auto;
}
.env-help {
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.env-footer .env-help:hover,
.env-footer .env-help:active {
  background: transparent;
}
.env-verification-error {
  margin-top: 12px;
}
</style>
