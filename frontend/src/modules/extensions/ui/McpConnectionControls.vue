<template>
  <section v-if="isPersonalMcp(server.auth_mode)" class="mcp-personal-connection" :class="{ 'has-credential-form': showCredentialForm }" aria-label="个人凭证" @click.stop>
    <div class="mcp-connection-heading">
      <h3>个人凭证</h3>
      <span class="mcp-connection-status" :class="{ 'needs-update': server.enabled && server.connection_status === 'reconnect_required' }" role="status">
        {{ server.enabled ? mcpConnectionLabel(server.connection_status, server.auth_mode) : '服务已停用' }}
      </span>
    </div>
    <p class="mcp-connection-description">智能体将使用你的个人凭证访问此服务。</p>

    <div v-if="showCredentialForm" class="mcp-credential-form">
      <label :for="inputId">{{ server.auth_mode === 'bearer' ? 'Token' : 'API Key' }}</label>
      <a-input-password :id="inputId" v-model:value="secret" :disabled="busy" placeholder="粘贴你的凭证" autocomplete="new-password" :maxlength="32768" :aria-describedby="`${inputId}-hint`" @pressEnter="save" />
      <p :id="`${inputId}-hint`">{{ server.auth_mode === 'bearer' ? '仅填写 Token，系统自动添加 Bearer 前缀' : `填写服务方提供的 API Key，发送到 ${server.api_key_header}` }}</p>
    </div>

    <div class="mcp-connection-actions">
      <a-button-group>
        <a-button v-if="showCredentialForm" type="primary" :loading="busy" :disabled="!secret" @click="save">保存凭证</a-button>
        <a-button v-else-if="server.connection_status === 'connected'" :class="{ 'is-tested': result }" :loading="busy" :disabled="!server.enabled" :title="result ? '重新测试连接' : undefined" aria-live="polite" @click="testConnection">
          <template #icon><Check v-if="result" :size="14" aria-hidden="true" /><Zap v-else :size="14" aria-hidden="true" /></template>
          {{ result || '测试连接' }}
        </a-button>
        <a-button v-else-if="!isStatic" type="primary" :loading="busy" :disabled="!server.enabled" @click="connect">授权</a-button>

        <a-button v-if="editing" :disabled="busy" @click="cancelEdit">取消</a-button>
        <a-dropdown v-else-if="server.connection_status !== 'disconnected'" v-model:open="menuOpen" :trigger="['click']" :disabled="busy" placement="bottomRight">
          <a-button :disabled="busy" aria-label="更多凭证操作" title="更多凭证操作" aria-haspopup="menu" :aria-expanded="menuOpen" class="lucide-icon-btn" @keydown.esc.stop="menuOpen = false">
            <template #icon><ChevronDown :size="14" aria-hidden="true" /></template>
          </a-button>
          <template #overlay>
            <a-menu @click="menuOpen = false" @keydown.esc.stop="menuOpen = false">
              <a-menu-item v-if="server.enabled && server.connection_status === 'connected'" key="replace" @click="isStatic ? (editing = true) : connect()">
                {{ isStatic ? '替换凭证' : '重新授权' }}
              </a-menu-item>
              <a-menu-divider v-if="server.enabled && server.connection_status === 'connected'" />
              <a-menu-item key="disconnect" danger @click="disconnect">{{ isStatic ? '清除凭证' : '断开连接' }}</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </a-button-group>
    </div>

    <p v-if="error" class="mcp-connection-error" role="alert"><AlertCircle :size="14" aria-hidden="true" />{{ error }}</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { AlertCircle, Check, ChevronDown, Zap } from '@lucide/vue'
import { mcpApi } from '@/apis/mcp_api'
import { isPersonalMcp, mcpConnectionLabel } from '@/modules/extensions/model/mcpOAuth'

const props = defineProps({ server: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const busy = ref(false)
const error = ref('')
const result = ref('')
const secret = ref('')
const editing = ref(false)
const menuOpen = ref(false)
const isStatic = computed(() => ['bearer', 'api_key'].includes(props.server.auth_mode))
const showCredentialForm = computed(() => isStatic.value && props.server.enabled && (editing.value || props.server.connection_status !== 'connected'))
const inputId = computed(() => `mcp-personal-secret-${props.server.slug}`)

watch([() => props.server.slug, () => props.server.auth_mode, () => props.server.config_revision], () => {
  cancelEdit()
  error.value = ''
  result.value = ''
})

/** 所有个人连接操作共享进行中与安全错误状态。 */
async function perform(action) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  result.value = ''
  try {
    await action()
  } catch (err) {
    error.value = err.message || '操作失败，请重试'
  } finally {
    busy.value = false
  }
}

/** 保存仅发布凭证，不宣称远端连接验证成功。 */
async function save() {
  if (!secret.value) return
  await perform(async () => {
    await mcpApi.saveCredentials(props.server.slug, secret.value, props.server.config_revision)
    cancelEdit()
    emit('changed')
  })
}

/** 取消替换时保留服务器上已保存的值。 */
function cancelEdit() {
  secret.value = ''
  editing.value = false
  menuOpen.value = false
}

/** OAuth 登录继续交给服务提供方。 */
async function connect() {
  await perform(async () => {
    const response = await mcpApi.connect(props.server.slug)
    window.location.assign(response.authorization_url)
  })
}

/** 测试只使用已保存的本人凭证，随后回读失效状态。 */
async function testConnection() {
  await perform(async () => {
    await mcpApi.testConnection(props.server.slug)
    result.value = '连接正常'
  })
  emit('changed')
}

/** 清除本人连接后回读持久状态。 */
async function disconnect() {
  await perform(async () => {
    await mcpApi.disconnect(props.server.slug)
    cancelEdit()
    emit('changed')
  })
}
</script>

<style lang="less" scoped>
.mcp-personal-connection {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 16px;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--border-surface);
  border-radius: 8px;
  background: var(--gray-25);
}

.mcp-connection-heading {
  grid-column: 1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;

  h3 { margin: 0; color: var(--gray-900); font-size: 14px; font-weight: 600; line-height: 22px; }
}

.mcp-connection-status {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--gray-100);
  color: var(--gray-700);
  font-size: 12px;
  line-height: 20px;

  &.needs-update { background: var(--color-warning-50); color: var(--color-warning-900); }
}

.mcp-connection-description { grid-column: 1; margin: 0; color: var(--color-text-secondary); font-size: 13px; line-height: 20px; }
.mcp-credential-form {
  grid-column: 1 / -1;
  max-width: 520px;
  margin-top: 12px;

  label { display: block; margin-bottom: 6px; color: var(--gray-800); font-size: 13px; }
  p { margin: 6px 0 0; color: var(--color-text-secondary); font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
}

.mcp-connection-actions { grid-column: 2; grid-row: 1 / 3; align-self: center; justify-self: end; }
.has-credential-form .mcp-connection-actions { grid-column: 1 / -1; grid-row: auto; margin-top: 8px; }
.mcp-connection-actions :deep(.is-tested svg) { color: var(--color-success-700); }
.mcp-connection-error {
  grid-column: 1 / -1;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 10px 0 0;
  color: var(--color-error-700);
  font-size: 12px;
  line-height: 20px;
  overflow-wrap: anywhere;

  svg { flex-shrink: 0; margin-top: 3px; }
}
</style>
