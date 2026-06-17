<template>
  <div class="tab-content">
    <div v-if="editing" class="edit-panel">
      <div class="edit-panel-header">
        <div>
          <h3>编辑 MCP</h3>
          <p>修改后保存会立即更新当前 MCP 配置。</p>
        </div>
        <div class="mode-slider" :class="{ 'is-json': formMode === 'json' }">
          <span class="mode-slider-thumb"></span>
          <button
            type="button"
            class="lucide-icon-btn mode-slider-btn"
            :class="{ active: formMode === 'form' }"
            title="表单模式"
            aria-label="切换到表单模式"
            @click="formMode = 'form'"
          >
            <Rows3 :size="14" />
          </button>
          <button
            type="button"
            class="lucide-icon-btn mode-slider-btn"
            :class="{ active: formMode === 'json' }"
            title="JSON 模式"
            aria-label="切换到 JSON 模式"
            @click="formMode = 'json'"
          >
            <Braces :size="14" />
          </button>
        </div>
      </div>

      <a-form v-if="formMode === 'form'" layout="vertical" class="extension-form inline-edit-form">
        <section class="form-section">
          <div class="form-section-title">
            <span>基础信息</span>
            <small>定义 MCP 的名称、描述与展示方式。</small>
          </div>
          <div class="form-grid form-grid-three">
            <a-form-item label="MCP 名称" required class="form-item">
              <a-input v-model:value="editForm.name" placeholder="请输入 MCP 名称（唯一标识）" disabled />
            </a-form-item>
            <a-form-item label="传输类型" required class="form-item">
              <a-select v-model:value="editForm.transport">
                <a-select-option value="streamable_http">streamable_http</a-select-option>
                <a-select-option value="sse">sse</a-select-option>
                <a-select-option value="stdio">stdio</a-select-option>
              </a-select>
            </a-form-item>
            <a-form-item label="图标" class="form-item">
              <a-input v-model:value="editForm.icon" placeholder="输入 emoji，如 🧠" :maxlength="2" />
            </a-form-item>
          </div>
          <a-form-item label="描述" class="form-item form-item-full">
            <a-textarea v-model:value="editForm.description" placeholder="请输入 MCP 描述" :rows="2" />
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title">
            <span>连接配置</span>
            <small>配置当前传输方式需要的连接参数。</small>
          </div>
          <template v-if="editForm.transport === 'streamable_http' || editForm.transport === 'sse'">
            <a-form-item label="MCP URL" required class="form-item form-item-full">
              <a-input v-model:value="editForm.url" placeholder="https://example.com/mcp" />
            </a-form-item>
            <div class="form-grid">
              <a-form-item label="HTTP 超时（秒）" class="form-item">
                <a-input-number
                  v-model:value="editForm.timeout"
                  :min="1"
                  :max="300"
                  style="width: 100%"
                />
              </a-form-item>
              <a-form-item label="SSE 读取超时（秒）" class="form-item">
                <a-input-number
                  v-model:value="editForm.sse_read_timeout"
                  :min="1"
                  :max="300"
                  style="width: 100%"
                />
              </a-form-item>
            </div>
          </template>
          <template v-if="isStdioTransport">
            <a-form-item label="命令" required class="form-item form-item-full">
              <a-input v-model:value="editForm.command" placeholder="例如：npx 或 /path/to/server" />
            </a-form-item>
          </template>
          <a-form-item label="标签" class="form-item form-item-full">
            <a-select
              v-model:value="editForm.tags"
              mode="tags"
              placeholder="输入标签后回车添加"
              style="width: 100%"
            />
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title">
            <span>高级配置</span>
            <small>请求头、启动参数和环境变量会直接影响 MCP 运行。</small>
          </div>
          <template v-if="editForm.transport === 'streamable_http' || editForm.transport === 'sse'">
            <a-form-item label="HTTP 请求头" class="form-item form-item-full">
              <a-textarea
                v-model:value="editForm.headersText"
                placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
                :rows="4"
                class="config-textarea"
              />
              <div class="form-helper">请输入合法 JSON 对象，留空表示不发送额外请求头。</div>
            </a-form-item>
          </template>
          <template v-if="isStdioTransport">
            <a-form-item label="参数" class="form-item form-item-full">
              <a-select
                v-model:value="editForm.args"
                mode="tags"
                placeholder="输入参数后回车添加，如：-m"
                style="width: 100%"
              />
            </a-form-item>
            <a-form-item label="环境变量" class="form-item form-item-full">
              <div class="env-editor-shell">
                <McpEnvEditor v-model="editForm.env" />
              </div>
            </a-form-item>
          </template>
          <McpAuthConfigBuilder v-model="editForm.authConfigText" :transport="editForm.transport" />
        </section>
      </a-form>

      <div v-else class="json-mode">
        <div class="json-mode-header">
          <span>JSON 配置</span>
          <small>适合批量调整完整 MCP 配置，保存前请确认 JSON 格式有效。</small>
        </div>
        <a-textarea
          v-model:value="jsonContent"
          :rows="15"
          placeholder="请输入 JSON 配置"
          class="json-textarea"
        />
        <div class="json-actions">
          <a-button size="small" @click="formatJson">格式化</a-button>
          <a-button size="small" @click="parseJsonToForm">解析到表单</a-button>
        </div>
      </div>

      <div class="edit-panel-actions">
        <a-button class="lucide-icon-btn" :disabled="editLoading" @click="cancelEdit">
          <template #icon><X :size="14" /></template>
          取消
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" :loading="editLoading" @click="handleSaveEdit">
          <template #icon><Save :size="14" /></template>
          保存
        </a-button>
      </div>
    </div>

    <div v-else class="info-grid">
      <div v-if="server.description" class="info-item">
        <label>描述</label>
        <span>{{ server.description }}</span>
      </div>
      <div class="info-item">
        <label>传输类型</label>
        <span>
          <a-tag :color="getTransportColor(server.transport)">{{ server.transport }}</a-tag>
        </span>
      </div>
      <div v-if="Array.isArray(server.tags) && server.tags.length > 0" class="info-item">
        <label>标签</label>
        <span>
          <a-tag v-for="tag in server.tags" :key="tag">{{ tag }}</a-tag>
        </span>
      </div>
      <template v-if="server.transport === 'streamable_http' || server.transport === 'sse'">
        <div v-if="server.url" class="info-item">
          <label>MCP URL</label>
          <span class="code-inline text-break-all">{{ server.url }}</span>
        </div>
        <div v-if="server.headers && Object.keys(server.headers).length > 0" class="info-item">
          <label>请求头</label>
          <pre class="code-pre">{{ JSON.stringify(server.headers, null, 2) }}</pre>
        </div>
        <div v-if="server.timeout" class="info-item">
          <label>HTTP 超时</label>
          <span>{{ server.timeout }} 秒</span>
        </div>
        <div v-if="server.sse_read_timeout" class="info-item">
          <label>SSE 读取超时</label>
          <span>{{ server.sse_read_timeout }} 秒</span>
        </div>
      </template>
      <template v-if="server.transport === 'stdio'">
        <div v-if="server.command" class="info-item">
          <label>命令</label>
          <span class="code-inline">{{ server.command }}</span>
        </div>
        <div v-if="server.args && server.args.length > 0" class="info-item">
          <label>参数</label>
          <span>
            <a-tag v-for="(arg, index) in server.args" :key="index" size="small">{{ arg }}</a-tag>
          </span>
        </div>
        <div v-if="server.env && Object.keys(server.env).length > 0" class="info-item">
          <label>环境变量</label>
          <pre class="code-pre">{{ JSON.stringify(server.env, null, 2) }}</pre>
        </div>
      </template>
      <div class="info-item">
        <label>创建时间</label>
        <span>{{ formatTime(server.created_at) }}</span>
      </div>
      <div class="info-item">
        <label>更新时间</label>
        <span>{{ formatTime(server.updated_at) }}</span>
      </div>
      <div class="info-item">
        <label>创建人</label>
        <span>{{ server.created_by }}</span>
      </div>
      <div
        v-if="server.auth_config && Object.keys(server.auth_config).length > 0"
        class="info-item info-item-full auth-config-info"
      >
        <label>认证配置明细</label>
        <McpAuthConfigBuilder
          :modelValue="JSON.stringify(server.auth_config, null, 2)"
          :transport="server.transport"
          readonly
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Braces, Rows3, Save, X } from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import { formatFullDateTime } from '@/utils/time'
import {
  buildMcpServerPayloadFromForm,
  createMcpServerFormState,
  formatMcpServerJsonContent,
  parseMcpServerJsonContent,
  stringifyMcpServerConfig,
  validateMcpServerPayload
} from '@/utils/mcpServerFormUtils'
import McpAuthConfigBuilder from '@/components/extensions/McpAuthConfigBuilder.vue'
import McpEnvEditor from '@/components/McpEnvEditor.vue'

const props = defineProps({
  server: { type: Object, required: true }
})

const emit = defineEmits(['saved'])
const editing = defineModel('editing', { type: Boolean, default: false })

const editLoading = shallowRef(false)
const formMode = shallowRef('form')
const jsonContent = shallowRef('')

const editForm = reactive(createMcpServerFormState())

const isStdioTransport = computed(
  () =>
    String(editForm.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

const formatTime = (timeStr) => formatFullDateTime(timeStr)

const getTransportColor = (transport) => {
  const colors = { sse: 'orange', stdio: 'green', streamable_http: 'blue' }
  return colors[transport] || 'blue'
}

const resetEditForm = (data) => {
  Object.assign(editForm, createMcpServerFormState(data))
  jsonContent.value = stringifyMcpServerConfig(data)
}

const cancelEdit = () => {
  editing.value = false
  resetEditForm(props.server)
}

const formatJson = () => {
  const formatted = formatMcpServerJsonContent(jsonContent.value, message.error)
  if (formatted !== undefined) jsonContent.value = formatted
}

const parseJsonToForm = () => {
  const data = parseMcpServerJsonContent(jsonContent.value, message.error)
  if (data === undefined) return
  resetEditForm(data)
  formMode.value = 'form'
  message.success('已解析到表单')
}

const buildEditPayload = () => {
  if (formMode.value === 'json') {
    return parseMcpServerJsonContent(jsonContent.value, message.error) || null
  }

  return buildMcpServerPayloadFromForm(editForm, message.error)
}

const handleSaveEdit = async () => {
  const data = buildEditPayload()
  if (!data || !validateMcpServerPayload(data, message.error)) return

  try {
    editLoading.value = true
    const result = await mcpApi.updateMcpServer(props.server.name, data)
    if (result.success) {
      message.success('MCP 更新成功')
      editing.value = false
      emit('saved')
    } else {
      message.error(result.message || '更新失败')
    }
  } catch (err) {
    message.error(err.message || '更新失败')
  } finally {
    editLoading.value = false
  }
}

watch(
  () => props.server,
  (server) => {
    resetEditForm(server)
  },
  { immediate: true }
)

watch(editing, (isEditing) => {
  if (isEditing) {
    formMode.value = 'form'
    resetEditForm(props.server)
  }
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.edit-panel {
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.025);

  .edit-panel-header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-start;
    margin-bottom: 18px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--gray-100);

    h3 {
      margin: 0 0 4px;
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }

    p {
      margin: 0;
      font-size: 13px;
      color: var(--gray-500);
    }
  }

  .mode-slider {
    position: relative;
    display: inline-grid;
    grid-template-columns: 1fr 1fr;
    width: 72px;
    height: 32px;
    padding: 3px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-50);
    flex-shrink: 0;
  }

  .mode-slider-thumb {
    position: absolute;
    top: 3px;
    left: 3px;
    width: 32px;
    height: 24px;
    border-radius: 6px;
    background: var(--gray-0);
    box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
    transition: transform 0.18s ease;
  }

  .mode-slider.is-json .mode-slider-thumb {
    transform: translateX(34px);
  }

  .mode-slider-btn {
    position: relative;
    z-index: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 24px;
    padding: 0;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-500);
    cursor: pointer;
    transition: color 0.15s ease;

    &.active {
      color: var(--main-color);
    }
  }

  .inline-edit-form {
    display: flex;
    flex-direction: column;

    :deep(.ant-form-item) {
      margin-bottom: 0;
    }

    :deep(.ant-form-item-label > label) {
      color: var(--gray-700);
      font-size: 13px;
      font-weight: 500;
    }
  }

  .form-section {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 0 0;

    & + .form-section {
      margin-top: 18px;
      border-top: 1px solid var(--gray-100);
    }

    &:first-child {
      padding-top: 0;
    }
  }

  .form-section-title,
  .json-mode-header {
    display: flex;
    flex-direction: column;
    gap: 3px;

    span {
      font-size: 14px;
      font-weight: 600;
      color: var(--gray-900);
    }

    small {
      font-size: 12px;
      color: var(--gray-500);
    }
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }

  .form-grid-three {
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(120px, 0.45fr);
  }

  .form-item-full {
    width: 100%;
  }

  .form-helper {
    margin-top: 6px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--gray-500);
  }

  .config-textarea,
  .json-textarea {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 13px;
    line-height: 1.6;
  }

  .env-editor-shell {
    padding: 0;
  }

  .json-mode {
    .json-mode-header {
      margin-bottom: 14px;
    }

    .json-actions {
      margin-top: 12px;
      display: flex;
      gap: 8px;
    }
  }

  .edit-panel-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid var(--gray-100);
  }
}

.auth-config-info {
  grid-column: 1 / -1;
  margin-top: 10px;

  label {
    margin-bottom: 8px;
    display: block;
  }
}
</style>
