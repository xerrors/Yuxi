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
      <div class="auth-provider-grid">
        <button
          v-for="option in providerOptions"
          :key="option.value"
          type="button"
          class="auth-provider-card" :disabled="readonly"
          :class="{ active: form.provider === option.value }"
          @click="switchProvider(option.value)"
        >
          <component :is="option.icon" :size="18" />
          <span>{{ option.label }}</span>
          <small>{{ option.description }}</small>
        </button>
      </div>

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
          <div class="binding-scope-grid">
            <button
              v-for="scope in bindingScopeOptions"
              :key="scope.value"
              type="button"
              class="binding-scope-card" :disabled="readonly"
              :class="{ active: form.bindingScope === scope.value }"
              @click="form.bindingScope = scope.value"
            >
              <component :is="scope.icon" :size="16" />
              <span>{{ scope.label }}</span>
              <small>{{ scope.description }}</small>
            </button>
          </div>
        </section>

        <section class="auth-config-section">
          <div class="section-heading">
            <span>注入到 MCP</span>
            <small>运行时会把 token、密钥或上下文写入请求头或环境变量。</small>
          </div>
          <div class="auth-field-row compact">
            <label>注入目标</label>
            <a-segmented
              v-model:value="form.injectTarget" :disabled="readonly"
              :options="injectTargetOptions"
              size="small"
            />
          </div>
          <div class="quick-template-bar" v-if="!readonly">
            <button
              v-for="entry in quickInjectEntries"
              :key="`${entry.name}:${entry.value_template}`"
              type="button"
              @click="addQuickInjectEntry(entry)"
            >
              {{ entry.label }}
            </button>
          </div>
          <div class="row-editor">
            <div
              v-for="(entry, index) in form.injectEntries"
              :key="index"
              class="row-editor-line"
            >
              <a-input v-model:value="entry.name" :readonly="readonly" placeholder="名称，如 Authorization" />
              <a-input
                v-model:value="entry.value_template"
                :readonly="readonly"
                placeholder="模板，如 Bearer ${access_token}"
              />
              <a-button
                type="text"
                size="small"
                danger
                :disabled="form.injectEntries.length === 1"
                @click="removeInjectEntry(index)" v-if="!readonly"
              >
                <Trash2 :size="14" />
              </a-button>
            </div>
            <a-button size="small" class="lucide-icon-btn" @click="addInjectEntry" v-if="!readonly">
              <Plus :size="13" />
              <span>添加注入项</span>
            </a-button>
          </div>
        </section>

        <section v-if="isTokenProvider" class="auth-config-section">
          <div class="section-heading">
            <span>Token 获取接口</span>
            <small>适配公司 API 网关、IAM 或其他内部换 token 服务。</small>
          </div>
          <div class="auth-form-grid">
            <div class="auth-field-row span-2">
              <label>Token 接口 URL</label>
              <a-input
                v-model:value="form.tokenUrl" :readonly="readonly"
                placeholder="例如：http://gateway.internal/api/token"
              />
            </div>
            <div class="auth-field-row">
              <label>请求方法</label>
              <a-select v-model:value="form.tokenMethod" :disabled="readonly">
                <a-select-option value="POST">POST</a-select-option>
                <a-select-option value="GET">GET</a-select-option>
                <a-select-option value="PUT">PUT</a-select-option>
              </a-select>
            </div>
            <div class="auth-field-row">
              <label>Body 类型</label>
              <a-select v-model:value="form.tokenBodyType" :disabled="readonly">
                <a-select-option value="json">JSON</a-select-option>
                <a-select-option value="form">Form</a-select-option>
              </a-select>
            </div>
          </div>

          <a-collapse ghost class="auth-inner-collapse">
            <a-collapse-panel key="request" header="请求参数">
              <div class="kv-section">
                <div class="kv-title">请求头</div>
                <div class="row-editor">
                  <div
                    v-for="(row, index) in form.tokenHeaders"
                    :key="`header-${index}`"
                    class="row-editor-line"
                  >
                    <a-input v-model:value="row.key" :readonly="readonly" placeholder="Header 名称" />
                    <a-input v-model:value="row.value" :readonly="readonly" placeholder="Header 值" />
                    <a-button
                      type="text"
                      size="small"
                      danger
                      :disabled="form.tokenHeaders.length === 1"
                      @click="removeKeyValueRow(form.tokenHeaders, index)" v-if="!readonly"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </div>
                  <a-button size="small" class="lucide-icon-btn" v-if="!readonly" @click="addKeyValueRow(form.tokenHeaders)">
                    <Plus :size="13" />
                    <span>添加一行</span>
                  </a-button>
                </div>
              </div>
              <div class="kv-section">
                <div class="kv-title">Body 模板</div>
                <div class="row-editor">
                  <div
                    v-for="(row, index) in form.tokenBodyTemplate"
                    :key="`body-${index}`"
                    class="row-editor-line"
                  >
                    <a-input v-model:value="row.key" :readonly="readonly" placeholder="字段名" />
                    <a-input v-model:value="row.value" :readonly="readonly" placeholder="模板值，如 ${secret.client_id}" />
                    <a-button
                      type="text"
                      size="small"
                      danger
                      :disabled="form.tokenBodyTemplate.length === 1"
                      @click="removeKeyValueRow(form.tokenBodyTemplate, index)" v-if="!readonly"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </div>
                  <a-button
                    size="small"
                    class="lucide-icon-btn"
                    v-if="!readonly" @click="addKeyValueRow(form.tokenBodyTemplate)"
                  >
                    <Plus :size="13" />
                    <span>添加一行</span>
                  </a-button>
                </div>
              </div>
            </a-collapse-panel>
            <a-collapse-panel key="response" header="响应映射">
              <div class="kv-section">
                <div class="kv-title">把网关响应映射为标准 token 字段</div>
                <div class="row-editor">
                  <div
                    v-for="(row, index) in form.tokenResponseMap"
                    :key="`response-${index}`"
                    class="row-editor-line"
                  >
                    <a-input v-model:value="row.key" :readonly="readonly" placeholder="标准字段，如 access_token" />
                    <a-input v-model:value="row.value" :readonly="readonly" placeholder="响应路径，如 data.access_token" />
                    <a-button
                      type="text"
                      size="small"
                      danger
                      :disabled="form.tokenResponseMap.length === 1"
                      @click="removeKeyValueRow(form.tokenResponseMap, index)" v-if="!readonly"
                    >
                      <Trash2 :size="14" />
                    </a-button>
                  </div>
                  <a-button
                    size="small"
                    class="lucide-icon-btn"
                    v-if="!readonly" @click="addKeyValueRow(form.tokenResponseMap)"
                  >
                    <Plus :size="13" />
                    <span>添加一行</span>
                  </a-button>
                </div>
              </div>
            </a-collapse-panel>
          </a-collapse>
        </section>

        <a-collapse ghost class="auth-advanced-collapse">
          <a-collapse-panel key="advanced" header="高级设置">
            <div class="auth-form-grid">
              <div class="auth-field-row">
                <label>MCP 清单隔离</label>
                <a-select v-model:value="form.manifestScope" :disabled="readonly">
                  <a-select-option value="binding">按连接隔离</a-select-option>
                  <a-select-option value="server">服务级共享</a-select-option>
                </a-select>
              </div>
              <div class="auth-field-row">
                <label>提前刷新秒数</label>
                <a-input-number
                  v-model:value="form.preRefreshSeconds" :disabled="readonly"
                  :min="0"
                  :max="86400"
                  style="width: 100%"
                />
              </div>
              <div class="auth-field-row span-2 compact">
                <label>401 处理</label>
                <a-switch v-model:checked="form.retryOnceOn401" :disabled="readonly" />
                <span class="field-helper">收到 401 时清理缓存并自动重试一次。</span>
              </div>
            </div>
          </a-collapse-panel>
        </a-collapse>
      </template>

      <div v-if="form.provider !== 'none'" class="auth-preview-panel">
        <div>
          <span class="preview-label">连接页需要填写</span>
          <div v-if="secretFields.length > 0" class="secret-chip-row">
            <span v-for="field in secretFields" :key="field" class="secret-chip">
              {{ getSecretFieldLabel(field) }}
            </span>
          </div>
          <p v-else>当前配置未引用 `${secret.xxx}`，连接页可不填长期密钥。</p>
        </div>
        <div>
          <span class="preview-label">可用模板变量</span>
          <p>
            <code>${access_token}</code>、<code>${secret.client_id}</code>、<code>${context.user_id}</code>、<code>${context.department_id}</code>
          </p>
        </div>
      </div>

      <a-alert
        v-if="formWarning"
        class="auth-warning"
        type="warning"
        show-icon
        :message="formWarning"
      />
    </template>

    <div v-else class="auth-json-mode">
      <div class="json-guide-grid">
        <div>
          <strong>常用字段</strong>
          <p><code>provider</code> 决定鉴权方式，<code>binding_scope</code> 决定连接隔离。</p>
        </div>
        <div>
          <strong>接口换 Token</strong>
          <p><code>token_request</code> 描述 URL、请求头、Body 模板和响应映射。</p>
        </div>
        <div>
          <strong>注入规则</strong>
          <p><code>inject.entries</code> 决定最终写入 MCP 请求头或环境变量。</p>
        </div>
      </div>
      <a-textarea
        v-model:value="jsonDraft"
        :rows="12"
        class="auth-json-textarea" :readonly="readonly"
        placeholder="粘贴 auth_config JSON；留空表示不启用动态鉴权"
      />
      <div class="json-action-row" v-if="!readonly">
        <a-button size="small" @click="formatJsonDraft">格式化</a-button>
        <a-button size="small" type="primary" @click="importJsonToForm">导入到向导</a-button>
      </div>
      <a-alert
        v-if="jsonError"
        class="auth-warning"
        type="warning"
        show-icon
        :message="jsonError"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  Building2,
  Code2,
  Copy,
  Globe2,
  KeyRound,
  Plus,
  ShieldOff,
  Shuffle,
  Trash2,
  UserRound
} from 'lucide-vue-next'
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

const configMode = ref('form')
const jsonDraft = ref('')
const jsonError = ref('')
const syncing = ref(false)
const lastEmittedValue = ref(null)
const form = reactive(createDefaultAuthBuilderForm())

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

const quickInjectEntries = computed(() => {
  if (form.injectTarget === 'env') {
    return [
      { label: 'Access Token 环境变量', name: 'MCP_ACCESS_TOKEN', value_template: '${access_token}' },
      { label: 'User ID 环境变量', name: 'YUXI_USER_ID', value_template: '${context.user_id}' },
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
      value_template: form.provider === 'bound_secret' ? 'Bearer ${secret.access_token}' : 'Bearer ${access_token}'
    },
    { label: '用户 ID', name: 'X-Yuxi-User', value_template: '${context.user_id}' },
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

const addInjectEntry = () => {
  form.injectEntries.push({ name: '', value_template: '' })
}

const removeInjectEntry = (index) => {
  if (form.injectEntries.length === 1) {
    form.injectEntries[0].name = ''
    form.injectEntries[0].value_template = ''
    return
  }
  form.injectEntries.splice(index, 1)
}

const addKeyValueRow = (rows) => {
  rows.push({ key: '', value: '' })
}

const removeKeyValueRow = (rows, index) => {
  if (rows.length === 1) {
    rows[0].key = ''
    rows[0].value = ''
    return
  }
  rows.splice(index, 1)
}

const addQuickInjectEntry = (entry) => {
  const existing = form.injectEntries.find((item) => item.name === entry.name)
  if (existing) {
    existing.value_template = entry.value_template
    return
  }
  form.injectEntries.push({
    name: entry.name,
    value_template: entry.value_template
  })
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

const getSecretFieldLabel = (fieldName) => {
  const labelMap = {
    api_key: 'API Key',
    client_id: 'Client ID',
    client_secret: 'Client Secret',
    access_token: 'Access Token',
    refresh_token: 'Refresh Token'
  }
  return labelMap[fieldName] || fieldName
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
@import '@/assets/css/extensions.less';

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

.auth-provider-grid,
.binding-scope-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 8px;
}

.auth-provider-card,
.binding-scope-card {
  display: flex;
  min-height: 88px;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 5px;
  padding: 11px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-700);
  cursor: pointer;
  text-align: left;

  span {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.35;
  }

  &.active {
    border-color: var(--main-color);
    background: var(--main-10);
    color: var(--main-color);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.7;
  }
}

.binding-scope-card {
  min-height: 76px;
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

.auth-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.auth-field-row {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;

  &.compact {
    flex-direction: row;
    align-items: center;
    gap: 10px;
  }

  &.span-2 {
    grid-column: 1 / -1;
  }

  label {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }
}

.field-helper {
  color: var(--gray-500);
  font-size: 12px;
}

.quick-template-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;

  button {
    padding: 3px 8px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-25);
    color: var(--gray-600);
    cursor: pointer;
    font-size: 12px;

    &:hover {
      border-color: var(--main-color);
      color: var(--main-color);
    }
  }
}

.row-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row-editor-line {
  display: grid;
  grid-template-columns: minmax(130px, 0.7fr) minmax(180px, 1.3fr) 36px;
  gap: 8px;
  align-items: center;
}

.auth-inner-collapse,
.auth-advanced-collapse {
  border-radius: 8px;
  background: var(--gray-25);
}

.kv-section {
  display: flex;
  flex-direction: column;
  gap: 8px;

  & + .kv-section {
    margin-top: 14px;
  }
}

.kv-title,
.preview-label {
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}

.auth-preview-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: 12px;
  padding: 12px 14px;
  border: 1px dashed var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);

  p {
    margin: 6px 0 0;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.6;
  }

  code {
    font-family: @mono-font;
  }
}

.secret-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.secret-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--main-10);
  color: var(--main-color);
  font-size: 12px;
  font-weight: 500;
}

.auth-warning {
  margin-top: 0;
}

.auth-json-mode {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.json-guide-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;

  > div {
    padding: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  strong {
    color: var(--gray-900);
    font-size: 13px;
  }

  p {
    margin: 6px 0 0;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.5;
  }

  code {
    font-family: @mono-font;
  }
}

.auth-json-textarea {
  font-family: @mono-font;
  font-size: 13px;
  line-height: 1.6;
}

.json-action-row {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .auth-builder-toolbar {
    flex-direction: column;
  }

  .auth-form-grid,
  .auth-preview-panel,
  .json-guide-grid {
    grid-template-columns: 1fr;
  }

  .row-editor-line {
    grid-template-columns: 1fr;
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
  
  :deep(.ant-input), :deep(.ant-input-number), :deep(.ant-select-selector) {
    border-color: transparent;
    background-color: transparent;
  }
  
  /* Retain some outline so they don't look like floating text completely? Let's just make it subtle */
  :deep(.ant-input[disabled]),
  :deep(.ant-input[readonly]),
  :deep(.ant-input-number-disabled),
  :deep(.ant-select-disabled .ant-select-selector) {
    border: 1px solid var(--gray-150);
    background-color: var(--gray-0);
  }

  .auth-provider-card:disabled,
  .binding-scope-card:disabled {
    cursor: default;
    opacity: 1;
    background: var(--gray-25);
  }

  .auth-provider-card.active:disabled,
  .binding-scope-card.active:disabled {
    color: var(--main-color);
    background: var(--main-10);
    border-color: var(--main-color);

    span {
      color: var(--main-color);
    }
  }

  .auth-provider-card:not(.active):disabled,
  .binding-scope-card:not(.active):disabled {
    span {
      color: var(--gray-700);
    }
    small {
      color: var(--gray-500);
    }
  }

  :deep(.ant-radio-button-wrapper-disabled) {
    color: var(--gray-700);
    background-color: var(--gray-0);
    border-color: var(--gray-150);
    cursor: default;
  }
}

</style>
