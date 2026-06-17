<template>
  <a-modal
    v-model:open="visible"
    :title="editMode ? '编辑 MCP' : '添加 MCP'"
    @ok="handleFormSubmit"
    :confirmLoading="formLoading"
    @cancel="visible = false"
    :maskClosable="false"
    width="min(780px, calc(100vw - 32px))"
    class="server-modal"
  >
    <div class="mode-switch">
      <a-radio-group v-model:value="formMode" button-style="solid" size="small">
        <a-radio-button value="form">表单模式</a-radio-button>
        <a-radio-button value="json">JSON 模式</a-radio-button>
      </a-radio-group>
    </div>

    <a-form v-if="formMode === 'form'" layout="vertical" class="extension-form">
      <a-form-item label="MCP 名称" required class="form-item">
        <a-input
          v-model:value="form.name"
          placeholder="请输入 MCP 名称（唯一标识）"
          :disabled="editMode"
        />
      </a-form-item>
      <a-form-item label="描述" class="form-item">
        <a-input v-model:value="form.description" placeholder="请输入 MCP 描述" />
      </a-form-item>
      <a-row :gutter="16">
        <a-col :span="12">
          <a-form-item label="传输类型" required class="form-item">
            <a-select v-model:value="form.transport">
              <a-select-option value="streamable_http">streamable_http</a-select-option>
              <a-select-option value="sse">sse</a-select-option>
              <a-select-option value="stdio">stdio</a-select-option>
            </a-select>
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="图标" class="form-item">
            <a-input v-model:value="form.icon" placeholder="输入 emoji，如 🧠" :maxlength="2" />
          </a-form-item>
        </a-col>
      </a-row>
      <template v-if="form.transport === 'streamable_http' || form.transport === 'sse'">
        <a-form-item label="MCP URL" required class="form-item">
          <a-input v-model:value="form.url" placeholder="https://example.com/mcp" />
        </a-form-item>
        <a-form-item label="HTTP 请求头" class="form-item">
          <a-textarea
            v-model:value="form.headersText"
            placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
            :rows="3"
          />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="HTTP 超时（秒）" class="form-item">
              <a-input-number
                v-model:value="form.timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="SSE 读取超时（秒）" class="form-item">
              <a-input-number
                v-model:value="form.sse_read_timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </template>
      <template v-if="isStdioTransport">
        <a-form-item label="命令" required class="form-item">
          <a-input v-model:value="form.command" placeholder="例如：npx 或 /path/to/server" />
        </a-form-item>
        <a-form-item label="参数" class="form-item">
          <a-select
            v-model:value="form.args"
            mode="tags"
            placeholder="输入参数后回车添加，如：-m"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="环境变量" class="form-item">
          <McpEnvEditor v-model="form.env" />
        </a-form-item>
      </template>
      <McpAuthConfigBuilder v-model="form.authConfigText" :transport="form.transport" />
      <a-form-item label="标签" class="form-item">
        <a-select
          v-model:value="form.tags"
          mode="tags"
          placeholder="输入标签后回车添加"
          style="width: 100%"
        />
      </a-form-item>
    </a-form>

    <div v-else class="json-mode">
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
  </a-modal>
</template>

<script setup>
import { reactive, computed, shallowRef, watch } from 'vue'
import { message } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'
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
  open: { type: Boolean, default: false },
  editMode: { type: Boolean, default: false },
  editData: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'submitted'])

const visible = computed({
  get: () => props.open,
  set: (val) => emit('update:open', val)
})

const formLoading = shallowRef(false)
const formMode = shallowRef('form')
const jsonContent = shallowRef('')

const form = reactive(createMcpServerFormState())

const isStdioTransport = computed(
  () =>
    String(form.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

const resetForm = (data) => {
  Object.assign(form, createMcpServerFormState(data))
  jsonContent.value = stringifyMcpServerConfig(data)
}

watch(
  () => props.open,
  (val) => {
    if (!val) return
    formMode.value = 'form'
    resetForm(props.editData)
  },
  { immediate: true }
)

const formatJson = () => {
  const formatted = formatMcpServerJsonContent(jsonContent.value, message.error)
  if (formatted !== undefined) jsonContent.value = formatted
}

const parseJsonToForm = () => {
  const data = parseMcpServerJsonContent(jsonContent.value, message.error)
  if (data === undefined) return
  resetForm(data)
  formMode.value = 'form'
  message.success('已解析到表单')
}

const handleFormSubmit = async () => {
  try {
    formLoading.value = true
    const data =
      formMode.value === 'json'
        ? parseMcpServerJsonContent(jsonContent.value, message.error)
        : buildMcpServerPayloadFromForm(form, message.error)
    if (!data || !validateMcpServerPayload(data, message.error)) return

    if (props.editMode) {
      const result = await mcpApi.updateMcpServer(data.name, data)
      if (result.success) {
        message.success('MCP 更新成功')
      } else {
        message.error(result.message || '更新失败')
        return
      }
    } else {
      const result = await mcpApi.createMcpServer(data)
      if (result.success) {
        message.success('MCP 创建成功')
      } else {
        message.error(result.message || '创建失败')
        return
      }
    }
    visible.value = false
    emit('submitted')
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    formLoading.value = false
  }
}
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.mode-switch {
  margin-bottom: 16px;
  text-align: right;
}

.json-mode {
  .json-textarea {
    font-family: 'Monaco', 'Consolas', monospace;
    font-size: 13px;
  }
  .json-actions {
    margin-top: 12px;
    display: flex;
    gap: 8px;
  }
}
</style>
