<template>
  <div class="mcp-auth-builder" :class="{ 'is-readonly': readonly }">
    <div class="auth-builder-toolbar">
      <div class="auth-builder-title">
        <span>认证配置</span>
        <small>选择认证模式后，系统会生成运行时可执行的 auth_config。</small>
      </div>
      <a-space :size="8" wrap>
        <a-radio-group v-model:value="configMode" button-style="solid" size="small">
          <a-radio-button value="form">向导配置</a-radio-button>
          <a-radio-button value="json">JSON 高级</a-radio-button>
        </a-radio-group>
        <a-button size="small" class="lucide-icon-btn" @click="copyCurrentJson">
          <Copy :size="13" />
          <span>复制 JSON</span>
        </a-button>
      </a-space>
    </div>

    <template v-if="configMode === 'form'">
      <McpAuthOptionGrid
        :model-value="form.provider"
        :options="providerOptions"
        :readonly="readonly"
        @select="switchProvider"
      />

      <a-alert
        v-if="form.provider === 'none'"
        type="info"
        show-icon
        message="不启用动态鉴权"
        description="适合无需鉴权，或已在上方 HTTP 请求头中配置固定 token 的 MCP。动态 token、按用户/部门隔离权限时请选择其他模式。"
      />

      <template v-else>
        <section class="auth-config-section">
          <div class="section-heading">
            <span>绑定范围</span>
            <small>决定连接页维护的凭据按什么范围生效。</small>
          </div>
          <McpAuthOptionGrid
            v-model="form.bindingScope"
            :options="bindingScopeOptions"
            :readonly="readonly"
            variant="compact"
            :icon-size="16"
          />
        </section>

        <McpAuthInjectionSection
          v-model:target="form.injectTarget"
          v-model:entries="form.injectEntries"
          :readonly="readonly"
          :target-options="injectTargetOptions"
          :quick-entries="quickInjectEntries"
        />

        <McpAuthTokenRequestSection
          v-if="isTokenProvider"
          v-model:url="form.tokenUrl"
          v-model:method="form.tokenMethod"
          v-model:body-type="form.tokenBodyType"
          v-model:headers="form.tokenHeaders"
          v-model:body-template="form.tokenBodyTemplate"
          v-model:response-map="form.tokenResponseMap"
          :readonly="readonly"
        />

        <McpAuthAdvancedSection
          v-model:manifest-scope="form.manifestScope"
          v-model:pre-refresh-seconds="form.preRefreshSeconds"
          v-model:retry-once-on401="form.retryOnceOn401"
          :readonly="readonly"
        />
      </template>

      <McpAuthPreviewPanel v-if="form.provider !== 'none'" :secret-fields="secretFields" />

      <a-alert
        v-if="formWarning"
        class="auth-warning"
        type="warning"
        show-icon
        :message="formWarning"
      />
    </template>

    <McpAuthJsonMode
      v-else
      v-model="jsonDraft"
      :readonly="readonly"
      :json-error="jsonError"
      @format="formatJsonDraft"
      @import="importJsonToForm"
    />
  </div>
</template>

<script setup>
import { computed, reactive, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  Building2,
  Code2,
  Copy,
  Globe2,
  KeyRound,
  ShieldOff,
  Shuffle,
  UserRound
} from 'lucide-vue-next'
import McpAuthAdvancedSection from '@/components/mcp/McpAuthAdvancedSection.vue'
import McpAuthInjectionSection from '@/components/mcp/McpAuthInjectionSection.vue'
import McpAuthJsonMode from '@/components/mcp/McpAuthJsonMode.vue'
import McpAuthOptionGrid from '@/components/mcp/McpAuthOptionGrid.vue'
import McpAuthPreviewPanel from '@/components/mcp/McpAuthPreviewPanel.vue'
import McpAuthTokenRequestSection from '@/components/mcp/McpAuthTokenRequestSection.vue'
import {
  authConfigToBuilderForm,
  buildAuthConfigFromBuilderForm,
  createDefaultAuthBuilderForm,
  extractSecretFieldNames,
  isAuthConfigSupportedByBuilder
} from '@/utils/mcpAuthConfigBuilder'

const props = defineProps({
  readonly: { type: Boolean, default: false },
  modelValue: { type: String, default: '' },
  transport: { type: String, default: 'streamable_http' }
})

const emit = defineEmits(['update:modelValue'])

const configMode = shallowRef('form')
const jsonDraft = shallowRef('')
const jsonError = shallowRef('')
const syncing = shallowRef(false)
const lastEmittedValue = shallowRef(null)
const form = reactive(createDefaultAuthBuilderForm())

const bindingScopeOptions = [
  {
    value: 'system',
    label: '全局共享',
    description: '全员使用同一组凭据',
    icon: Globe2
  },
  {
    value: 'department',
    label: '部门共享',
    description: '按部门隔离权限',
    icon: Building2
  },
  {
    value: 'user',
    label: '个人专用',
    description: '按用户隔离权限',
    icon: UserRound
  }
]

const injectTargetOptions = [
  { label: '请求头', value: 'headers' },
  { label: '环境变量', value: 'env' }
]

const providerOptions = computed(() => {
  const options = [
    {
      value: 'none',
      label: '不启用',
      description: '无动态 token，或继续使用固定请求头。',
      icon: ShieldOff
    },
    {
      value: 'bound_secret',
      label: '绑定长期密钥',
      description: '连接页维护 API Key、长期 token 等。',
      icon: KeyRound
    },
    {
      value: 'custom_http_token',
      label: '接口换 Token',
      description: '调用内部网关或 IAM 动态获取 token。',
      icon: Shuffle
    }
  ]

  if (props.transport === 'stdio') {
    options.push({
      value: 'stdio_env',
      label: 'StdIO 环境变量',
      description: '把连接密钥注入本地进程环境变量。',
      icon: Code2
    })
  }

  return options
})

const quickInjectEntries = computed(() => {
  if (form.injectTarget === 'env') {
    return [
      { label: 'Access Token 环境变量', name: 'MCP_ACCESS_TOKEN', value_template: '${access_token}' },
      { label: 'User ID 环境变量', name: 'YUXI_USER_ID', value_template: '${context.user_id}' },
      { label: 'Work ID 环境变量', name: 'YUXI_WORK_ID', value_template: '${context.work_id}' },
      {
        label: 'Department ID 环境变量',
        name: 'YUXI_DEPARTMENT_ID',
        value_template: '${context.department_id}'
      }
    ]
  }
  return [
    {
      label: 'Authorization Bearer',
      name: 'Authorization',
      value_template:
        form.provider === 'bound_secret' ? 'Bearer ${secret.access_token}' : 'Bearer ${access_token}'
    },
    { label: '用户 ID', name: 'X-Yuxi-User', value_template: '${context.user_id}' },
    { label: '员工工号', name: 'X-Yuxi-Work-Id', value_template: '${context.work_id}' },
    { label: '部门 ID', name: 'X-Yuxi-Department', value_template: '${context.department_id}' },
    { label: 'API Key', name: 'X-Api-Key', value_template: '${secret.api_key}' }
  ]
})

const isTokenProvider = computed(() =>
  ['custom_http_token', 'client_credentials'].includes(form.provider)
)

const currentConfig = computed(() => {
  if (configMode.value === 'form') {
    return buildAuthConfigFromBuilderForm(form)
  }
  const trimmed = String(jsonDraft.value || '').trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
})

const secretFields = computed(() => extractSecretFieldNames(currentConfig.value || {}))

const formWarning = computed(() => {
  if (form.provider === 'none') return ''
  if (isTokenProvider.value && !String(form.tokenUrl || '').trim()) {
    return '接口换 Token 需要填写 Token 接口 URL，否则保存后运行时无法换取 token。'
  }
  if ((form.injectEntries || []).every((entry) => !String(entry.name || '').trim())) {
    return '至少添加一条注入项，否则获取到的 token 或密钥不会传给 MCP。'
  }
  if (
    isTokenProvider.value &&
    (form.tokenResponseMap || []).every((row) => row.key !== 'access_token')
  ) {
    return '响应映射建议包含 access_token，否则无法注入 Bearer token。'
  }
  return ''
})

const getCurrentJsonText = () => {
  if (configMode.value === 'json') {
    return jsonDraft.value
  }
  const config = buildAuthConfigFromBuilderForm(form)
  return config ? JSON.stringify(config, null, 2) : ''
}

const emitValue = (value) => {
  lastEmittedValue.value = value
  emit('update:modelValue', value)
}

const emitFormValue = () => {
  if (syncing.value || configMode.value !== 'form') return
  const text = getCurrentJsonText()
  jsonDraft.value = text
  emitValue(text)
}

const applyModelValue = (value) => {
  const text = String(value || '')
  jsonDraft.value = text
  jsonError.value = ''

  if (!text.trim()) {
    Object.assign(form, createDefaultAuthBuilderForm())
    configMode.value = 'form'
    return
  }

  try {
    const parsed = JSON.parse(text)
    if (!isAuthConfigSupportedByBuilder(parsed)) {
      configMode.value = 'json'
      jsonError.value = '当前 JSON 使用了高级 provider，建议在 JSON 高级模式维护。'
      return
    }
    Object.assign(form, authConfigToBuilderForm(parsed))
    configMode.value = 'form'
    jsonDraft.value = JSON.stringify(parsed, null, 2)
  } catch {
    configMode.value = 'json'
    jsonError.value = 'JSON 格式暂时无法解析，请修正后再导入到向导。'
  }
}

const switchProvider = (provider) => {
  Object.assign(form, createDefaultAuthBuilderForm(provider))
}

const copyCurrentJson = async () => {
  try {
    await navigator.clipboard.writeText(getCurrentJsonText())
    message.success('认证配置 JSON 已复制')
  } catch {
    message.error('复制失败')
  }
}

const formatJsonDraft = () => {
  const trimmed = String(jsonDraft.value || '').trim()
  if (!trimmed) {
    jsonDraft.value = ''
    emitValue('')
    return
  }
  try {
    const parsed = JSON.parse(trimmed)
    jsonDraft.value = JSON.stringify(parsed, null, 2)
    jsonError.value = ''
    emitValue(jsonDraft.value)
  } catch {
    jsonError.value = 'JSON 格式错误，无法格式化。'
  }
}

const importJsonToForm = () => {
  const trimmed = String(jsonDraft.value || '').trim()
  if (!trimmed) {
    Object.assign(form, createDefaultAuthBuilderForm())
    configMode.value = 'form'
    emitValue('')
    message.success('已切换为不启用动态鉴权')
    return
  }
  try {
    const parsed = JSON.parse(trimmed)
    if (!isAuthConfigSupportedByBuilder(parsed)) {
      jsonError.value = '该 JSON 使用高级 provider，暂不支持导入向导；可以继续在 JSON 高级模式保存。'
      return
    }
    Object.assign(form, authConfigToBuilderForm(parsed))
    configMode.value = 'form'
    jsonError.value = ''
    emitFormValue()
    message.success('已导入到向导配置')
  } catch {
    jsonError.value = 'JSON 格式错误，无法导入。'
  }
}

watch(
  () => props.modelValue,
  (value) => {
    if (value === lastEmittedValue.value) return
    syncing.value = true
    applyModelValue(value)
    syncing.value = false
  },
  { immediate: true }
)

watch(form, emitFormValue, { deep: true })

watch(jsonDraft, (value) => {
  if (syncing.value || configMode.value !== 'json') return
  emitValue(value)
})
</script>

<style lang="less" scoped>
.mcp-auth-builder {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-25);
}

.auth-builder-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.auth-builder-title,
.section-heading {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;

  span {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.5;
  }
}

.auth-config-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.auth-warning {
  margin-top: 0;
}

@media (max-width: 720px) {
  .auth-builder-toolbar {
    flex-direction: column;
  }
}

.is-readonly {
  :deep(.ant-input[disabled]),
  :deep(.ant-input[readonly]),
  :deep(.ant-input-number-disabled),
  :deep(.ant-select-disabled .ant-select-selector),
  :deep(.ant-switch-disabled) {
    color: var(--gray-900);
    background-color: var(--gray-0);
    border-color: transparent;
    cursor: default;
  }

  :deep(.ant-input),
  :deep(.ant-input-number),
  :deep(.ant-select-selector) {
    border-color: transparent;
    background-color: transparent;
  }

  :deep(.ant-input[disabled]),
  :deep(.ant-input[readonly]),
  :deep(.ant-input-number-disabled),
  :deep(.ant-select-disabled .ant-select-selector) {
    border: 1px solid var(--gray-150);
    background-color: var(--gray-0);
  }

  :deep(.auth-option-card:disabled) {
    cursor: default;
    opacity: 1;
    background: var(--gray-25);
  }

  :deep(.auth-option-card.active:disabled) {
    color: var(--main-color);
    background: var(--main-10);
    border-color: var(--main-color);
  }

  :deep(.auth-option-card.active:disabled span) {
    color: var(--main-color);
  }

  :deep(.auth-option-card:not(.active):disabled span) {
    color: var(--gray-700);
  }

  :deep(.auth-option-card:not(.active):disabled small) {
    color: var(--gray-500);
  }

  :deep(.ant-radio-button-wrapper-disabled) {
    color: var(--gray-700);
    background-color: var(--gray-0);
    border-color: var(--gray-150);
    cursor: default;
  }
}
</style>
