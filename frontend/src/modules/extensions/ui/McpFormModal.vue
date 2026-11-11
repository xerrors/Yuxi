<template>
  <a-modal
    v-model:open="visible"
    @ok="handleFormSubmit"
    :confirmLoading="formLoading"
    @cancel="visible = false"
    :maskClosable="false"
    :keyboard="!formLoading"
    :closable="!formLoading"
    :body-style="{ maxHeight: 'min(68dvh, 600px)', overflowY: 'auto', overflowX: 'hidden' }"
    width="min(680px, calc(100vw - 32px))"
    centered
    class="mcp-form-modal"
  >
    <template #title>
      <div class="mcp-form-heading">
        <span class="mcp-form-icon"><Plug :size="18" aria-hidden="true" /></span>
        <div>
          <span>{{ editMode ? '编辑连接器' : '新增连接器' }}</span>
          <p>连接外部服务，供智能体调用工具</p>
        </div>
      </div>
    </template>
    <a-form layout="vertical" class="extension-form mcp-form" :disabled="formLoading">
      <div class="mcp-form-grid">
        <a-form-item label="名称" html-for="mcp-form-name" required class="form-item">
          <a-input id="mcp-form-name" v-model:value="form.name" placeholder="如：文档搜索" />
        </a-form-item>
        <a-form-item label="标识" html-for="mcp-form-slug" required class="form-item">
          <a-input
            id="mcp-form-slug"
            v-model:value="form.slug"
            placeholder="如：docs-search"
            :disabled="editMode"
          />
        </a-form-item>
      </div>
      <a-form-item label="描述" html-for="mcp-form-description" class="form-item">
        <a-textarea
          id="mcp-form-description"
          v-model:value="form.description"
          placeholder="简要说明这个服务能做什么（可选）"
          :rows="2"
        />
      </a-form-item>
      <div class="mcp-form-grid endpoint-row">
        <a-form-item label="服务地址" html-for="mcp-form-url" required class="form-item">
          <a-input id="mcp-form-url" v-model:value="form.url" placeholder="https://example.com/mcp" />
        </a-form-item>
        <a-form-item label="传输方式" html-for="mcp-form-transport" required class="form-item">
          <a-select id="mcp-form-transport" v-model:value="form.transport">
            <a-select-option value="http">Streamable HTTP</a-select-option>
            <a-select-option value="sse">SSE</a-select-option>
          </a-select>
        </a-form-item>
      </div>
      <a-form-item label="接入方式" html-for="mcp-oauth-auth-mode" required>
        <a-select id="mcp-oauth-auth-mode" v-model:value="form.auth_mode" placeholder="请选择接入方式">
          <a-select-option value="none">无需认证</a-select-option>
          <a-select-opt-group label="用户各自提供凭证">
            <a-select-option value="bearer">每名用户填写 Bearer Token</a-select-option>
            <a-select-option value="api_key">每名用户填写 API Key</a-select-option>
            <a-select-option value="oauth">每名用户 OAuth 授权</a-select-option>
          </a-select-opt-group>
          <a-select-opt-group label="管理员统一提供凭证">
            <a-select-option value="headers">共享请求头（所有用户共用）</a-select-option>
          </a-select-opt-group>
        </a-select>
        <p v-if="authDescription" class="form-hint">{{ authDescription }}</p>
        <p v-if="clearsSharedHeaders" class="form-hint">{{ form.auth_mode === 'none' ? '保存时清除原共享请求头。' : '保存时清除原共享请求头，每名用户需要配置自己的凭证。' }}</p>
      </a-form-item>
      <a-form-item v-if="form.auth_mode === 'headers'" label="共享请求头（JSON）" html-for="mcp-form-headers">
        <a-textarea id="mcp-form-headers" v-model:value="form.headersText" placeholder='{"Authorization": "Bearer 你的共享 Token"}' :rows="3" aria-describedby="mcp-shared-headers-hint" />
        <div id="mcp-shared-headers-hint" class="form-hint">
          <p>Bearer Token 示例：<code>{"Authorization": "Bearer &lt;Token&gt;"}</code></p>
          <p>API Key 示例：<code>{"X-API-Key": "&lt;Key&gt;"}</code>，请求头名称以服务文档为准。</p>
        </div>
      </a-form-item>
      <a-form-item v-if="form.auth_mode === 'api_key'" label="认证请求头名称" html-for="mcp-api-key-header" required>
        <a-input id="mcp-api-key-header" v-model:value="form.api_key_header" placeholder="X-API-Key" :maxlength="128" />
        <p class="form-hint">按服务文档填写，例如 X-API-Key 或 X-Tencent-Meeting-Token。用户只需填写原始 Key 或 Token。</p>
      </a-form-item>
      <template v-if="form.auth_mode === 'oauth'">
        <div class="mcp-form-grid oauth-row">
          <a-form-item label="授权服务器 Issuer" html-for="mcp-oauth-issuer" required class="form-item"><a-input id="mcp-oauth-issuer" v-model:value="form.issuer_url" placeholder="https://sso.example.com/realms/company" /></a-form-item>
          <a-form-item label="客户端注册方式" html-for="mcp-oauth-registration" class="form-item">
            <a-select id="mcp-oauth-registration" v-model:value="form.registration"><a-select-option value="static">预注册客户端</a-select-option><a-select-option value="dynamic">动态注册</a-select-option></a-select>
          </a-form-item>
        </div>
        <div v-if="form.registration === 'static'" class="mcp-form-grid">
          <a-form-item label="Client ID" html-for="mcp-oauth-client-id" required class="form-item"><a-input id="mcp-oauth-client-id" v-model:value="form.client_id" /></a-form-item>
          <div class="form-item">
            <div class="mcp-secret-heading">
              <label for="mcp-oauth-client-secret">Client Secret</label>
              <a-checkbox v-if="editData?.oauth_client_secret_set" v-model:checked="form.clear_client_secret" aria-label="清除已保存 Client Secret" title="清除已保存 Client Secret">清除已保存</a-checkbox>
            </div>
            <a-form-item><a-input-password id="mcp-oauth-client-secret" v-model:value="form.client_secret" :disabled="form.clear_client_secret" :placeholder="editData?.oauth_client_secret_set ? '已配置，留空保留' : '按授权服务器要求填写'" autocomplete="new-password" /></a-form-item>
          </div>
        </div>
        <a-form-item label="授权范围" html-for="mcp-oauth-scopes"><a-input id="mcp-oauth-scopes" v-model:value="form.scopes" placeholder="空格分隔的 scopes" /></a-form-item>
      </template>
      <div class="mcp-connection-options">
        <button
          type="button"
          class="connection-options-toggle"
          :aria-expanded="connectionOptionsOpen"
          aria-controls="mcp-connection-options-content"
          @click="connectionOptionsOpen = !connectionOptionsOpen"
        >
          <ChevronRight :size="14" aria-hidden="true" />
          <span>高级连接选项</span>
          <span class="connection-options-hint">{{ form.auth_mode === 'headers' ? '超时设置' : '公共请求头与超时' }}</span>
        </button>
        <CollapseTransition>
          <div v-show="connectionOptionsOpen" id="mcp-connection-options-content" class="connection-options-content">
            <a-form-item v-if="form.auth_mode !== 'headers'" label="公共请求头（可选）" html-for="mcp-form-headers" class="form-item">
              <a-textarea
                id="mcp-form-headers"
                v-model:value="form.headersText"
                placeholder="JSON 格式，仅填写服务要求的版本号等非敏感协议字段"
                :rows="3"
              />
            </a-form-item>
            <div class="mcp-form-grid">
              <a-form-item label="HTTP 超时（秒）" html-for="mcp-form-timeout" class="form-item">
                <a-input-number id="mcp-form-timeout" v-model:value="form.timeout" :min="1" :max="300" placeholder="默认" />
              </a-form-item>
              <a-form-item label="SSE 读取超时（秒）" html-for="mcp-form-read-timeout" class="form-item">
                <a-input-number id="mcp-form-read-timeout" v-model:value="form.sse_read_timeout" :min="1" :max="300" placeholder="默认" />
              </a-form-item>
            </div>
          </div>
        </CollapseTransition>
      </div>
    </a-form>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ChevronRight, Plug } from '@lucide/vue'
import { mcpApi } from '@/apis/mcp_api'
import { parseMcpManifest } from '@/modules/extensions/model/mcpManifest'
import CollapseTransition from '@/shared/ui/CollapseTransition.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  editMode: { type: Boolean, default: false },
  editData: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'submitted'])

const formLoading = ref(false)
const connectionOptionsOpen = ref(false)

const visible = computed({
  get: () => props.open,
  set: (val) => { if (!formLoading.value) emit('update:open', val) }
})

/** 创建独立的空表单，供首次初始化与新增重置使用。 */
function createEmptyForm() {
  return {
    auth_mode: undefined, api_key_header: 'X-API-Key', issuer_url: '', registration: 'static', client_id: '', client_secret: '', clear_client_secret: false, scopes: '',
    slug: '',
    name: '',
    description: '',
    transport: 'http',
    url: '',
    headersText: '',
    timeout: null,
    sse_read_timeout: null,
  }
}

const form = reactive(createEmptyForm())
const authDescription = computed(() => ({
  none: '适用于公开服务，调用时不发送认证凭证。',
  bearer: '每位用户在连接器卡片填写自己的 Token，系统通过 Authorization: Bearer 发送。',
  api_key: '管理员指定认证请求头名称，每位用户在连接器卡片填写自己的 Key 或 Token。',
  oauth: '管理员登记应用信息，每位用户在连接器卡片登录并授权自己的服务账号。',
  headers: '管理员在下方填写凭证。所有人的请求都会使用这组请求头，访问同一个服务账号。',
})[form.auth_mode])
const clearsSharedHeaders = computed(() => props.editMode && (props.editData?.auth_mode || 'headers') === 'headers' && ['none', 'bearer', 'api_key', 'oauth'].includes(form.auth_mode))

watch(() => form.auth_mode, (mode, previous) => {
  if (previous === 'headers' && ['none', 'bearer', 'api_key', 'oauth'].includes(mode)) form.headersText = ''
}, { flush: 'sync' })

watch(
  () => props.open,
  (val) => {
    if (val) connectionOptionsOpen.value = props.editMode
    if (val && props.editData) {
      Object.assign(form, {
        auth_mode: props.editData.auth_mode || 'headers',
        api_key_header: props.editData.api_key_header || 'X-API-Key',
        issuer_url: props.editData.oauth_config?.issuer_url || '',
        registration: props.editData.oauth_config?.registration || 'static',
        client_id: props.editData.oauth_config?.client_id || '',
        client_secret: '', clear_client_secret: false, scopes: props.editData.oauth_config?.scopes || '',
        slug: props.editData.slug || '',
        name: props.editData.name || '',
        description: props.editData.description || '',
        transport: props.editData.transport === 'streamable_http' ? 'http' : props.editData.transport || 'http',
        url: props.editData.url || '',
        headersText: props.editData.headers ? JSON.stringify(props.editData.headers, null, 2) : '',
        timeout: props.editData.timeout,
        sse_read_timeout: props.editData.sse_read_timeout,
      })
    } else if (val && !props.editData) {
      Object.assign(form, createEmptyForm())
    }
  },
  { immediate: true }
)

const handleFormSubmit = async () => {
  if (formLoading.value) return
  try {
    formLoading.value = true
    let headers
    if (form.headersText.trim()) {
      try {
        headers = JSON.parse(form.headersText)
      } catch {
        message.error('请求头 JSON 格式错误')
        return
      }
    } else if (props.editMode) {
      headers = {}
    }
    if (!form.slug?.trim()) {
      message.error('连接器标识不能为空')
      return
    }
    if (!form.name?.trim()) {
      message.error('连接器名称不能为空')
      return
    }
    if (!form.auth_mode) {
      message.error('请选择接入方式')
      return
    }
    if (form.auth_mode === 'api_key' && !form.api_key_header?.trim()) {
      message.error('API Key 请求头名称不能为空')
      return
    }
    const entry = {
      type: form.transport,
      url: form.url,
      ...(headers !== undefined && { headers }),
      ...(form.timeout != null && { timeout: form.timeout }),
      ...(form.sse_read_timeout != null && { sse_read_timeout: form.sse_read_timeout }),
      extra_data: {
        name: form.name,
        ...(form.description && { description: form.description }),
      }
    }
    const [data] = parseMcpManifest(JSON.stringify({ mcpServers: { [form.slug]: entry } }))
    data.auth_mode = form.auth_mode
    if (form.auth_mode === 'api_key') data.api_key_header = form.api_key_header
    if (form.auth_mode === 'oauth') {
      data.oauth_config = {
        issuer_url: form.issuer_url, registration: form.registration, client_id: form.client_id,
        scopes: form.scopes,
      }
      if (form.clear_client_secret) {
        data.oauth_config.client_secret = ''
      } else if (form.client_secret) {
        data.oauth_config.client_secret = form.client_secret
      }
    }
    let submitted
    if (props.editMode) {
      const { slug, ...updateData } = data
      const result = await mcpApi.updateMcpServer(props.editData?.slug || slug, updateData)
      if (result.success) {
        submitted = result.data
        message.success('连接器更新成功')
      } else {
        message.error(result.message || '更新失败')
        return
      }
    } else {
      const result = await mcpApi.createMcpServer(data)
      if (result.success) {
        submitted = result.data
        message.success('连接器创建成功')
      } else {
        message.error(result.message || '创建失败')
        return
      }
    }
    formLoading.value = false
    visible.value = false
    emit('submitted', submitted)
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    formLoading.value = false
  }
}
</script>

<style lang="less" scoped>
.mcp-form-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-right: 32px;
  color: var(--gray-900);
  font-weight: 600;

  p {
    margin: 3px 0 0;
    color: var(--gray-600);
    font-size: 12px;
    font-weight: 400;
  }
}

.mcp-form-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--gray-100);
  color: var(--gray-700);
}

.mcp-form {
  padding-top: 16px;

  :deep(.ant-input-number), :deep(.ant-select) { width: 100%; }
  :deep(.ant-form-item-label > label) { color: var(--gray-800); font-size: 13px; }
}

.form-hint {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 20px;
  overflow-wrap: anywhere;

  p { margin: 0; }
  code { color: var(--gray-800); font-size: inherit; }
}

.mcp-secret-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  margin-bottom: 8px;
  color: var(--gray-800);
  font-size: 13px;

  :deep(.ant-checkbox-wrapper) { font-size: 12px; }
}

.mcp-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;

  > .form-item { min-width: 0; }
  &.endpoint-row { grid-template-columns: minmax(0, 1fr) 190px; }
  &.oauth-row { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); }
}

.mcp-connection-options {
  border-top: 1px solid var(--gray-150);

  .connection-options-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 44px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--gray-800);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;

    &:hover { color: var(--main-700); }
    &:focus-visible { outline: 2px solid var(--main-300); outline-offset: -2px; }
    svg { flex-shrink: 0; transition: transform 0.25s ease; }
    &[aria-expanded='true'] svg { transform: rotate(90deg); }
  }

  .connection-options-content { padding-top: 8px; }
  .connection-options-hint { margin-left: auto; color: var(--gray-600); font-size: 12px; }
  .mcp-form-grid .form-item { margin-bottom: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .mcp-connection-options .connection-options-content,
  .mcp-connection-options .connection-options-toggle svg { transition: none; }
}

@media (max-width: 600px) {
  .mcp-form-grid, .mcp-form-grid.endpoint-row, .mcp-form-grid.oauth-row { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .mcp-connection-options .mcp-form-grid { gap: 16px; }
  .mcp-form :deep(.ant-input), .mcp-form :deep(.ant-select-selector), .mcp-form :deep(.ant-input-number) { min-height: 40px; }
  :global(.mcp-form-modal .ant-modal-footer .ant-btn) { min-height: 44px; }
}
</style>
