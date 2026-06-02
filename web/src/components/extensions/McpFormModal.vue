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
import { ref, reactive, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'
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

const formLoading = ref(false)
const formMode = ref('form')
const jsonContent = ref('')

const form = reactive({
  name: '',
  description: '',
  transport: 'streamable_http',
  url: '',
  command: '',
  args: [],
  env: null,
  headersText: '',
  authConfigText: '',
  timeout: null,
  sse_read_timeout: null,
  tags: [],
  icon: ''
})

const isStdioTransport = computed(
  () =>
    String(form.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

watch(
  () => props.open,
  (val) => {
    if (val && props.editData) {
      formMode.value = 'form'
      Object.assign(form, {
        name: props.editData.name || '',
        description: props.editData.description || '',
        transport: props.editData.transport || 'streamable_http',
        url: props.editData.url || '',
        command: props.editData.command || '',
        args: props.editData.args || [],
        env: props.editData.env || null,
        headersText: props.editData.headers ? JSON.stringify(props.editData.headers, null, 2) : '',
        authConfigText: props.editData.auth_config
          ? JSON.stringify(props.editData.auth_config, null, 2)
          : '',
        timeout: props.editData.timeout,
        sse_read_timeout: props.editData.sse_read_timeout,
        tags: props.editData.tags || [],
        icon: props.editData.icon || ''
      })
      jsonContent.value = props.editData ? JSON.stringify(props.editData, null, 2) : ''
    } else if (val && !props.editData) {
      formMode.value = 'form'
      Object.assign(form, {
        name: '',
        description: '',
        transport: 'streamable_http',
        url: '',
        command: '',
        args: [],
        env: null,
        headersText: '',
        authConfigText: '',
        timeout: null,
        sse_read_timeout: null,
        tags: [],
        icon: ''
      })
      jsonContent.value = ''
    }
  },
  { immediate: true }
)

const formatJson = () => {
  try {
    const obj = JSON.parse(jsonContent.value)
    jsonContent.value = JSON.stringify(obj, null, 2)
  } catch {
    message.error('JSON 格式错误，无法格式化')
  }
}

const parseJsonToForm = () => {
  try {
    const obj = JSON.parse(jsonContent.value)
    Object.assign(form, {
      name: obj.name || '',
      description: obj.description || '',
      transport: obj.transport || 'streamable_http',
      url: obj.url || '',
      command: obj.command || '',
      args: obj.args || [],
      env: obj.env || null,
      headersText: obj.headers ? JSON.stringify(obj.headers, null, 2) : '',
      authConfigText: obj.auth_config ? JSON.stringify(obj.auth_config, null, 2) : '',
      timeout: obj.timeout || null,
      sse_read_timeout: obj.sse_read_timeout || null,
      tags: obj.tags || [],
      icon: obj.icon || ''
    })
    formMode.value = 'form'
    message.success('已解析到表单')
  } catch {
    message.error('JSON 格式错误')
  }
}

const handleFormSubmit = async () => {
  try {
    formLoading.value = true
    let data
    if (formMode.value === 'json') {
      try {
        data = JSON.parse(jsonContent.value)
      } catch {
        message.error('JSON 格式错误')
        return
      }
    } else {
      let headers = null
      if (form.headersText.trim()) {
        try {
          headers = JSON.parse(form.headersText)
        } catch {
          message.error('请求头 JSON 格式错误')
          return
        }
      }

      let authConfig = null
      if (form.authConfigText.trim()) {
        try {
          authConfig = JSON.parse(form.authConfigText)
        } catch {
          message.error('认证配置 JSON 格式错误')
          return
        }
      }
      data = {
        name: form.name,
        description: form.description || null,
        transport: form.transport,
        url: form.url || null,
        command: form.command || null,
        args: form.args.length > 0 ? form.args : null,
        env: form.env,
        headers,
        auth_config: authConfig,
        timeout: form.timeout || null,
        sse_read_timeout: form.sse_read_timeout || null,
        tags: form.tags.length > 0 ? form.tags : null,
        icon: form.icon || null
      }
    }
    if (!data.name?.trim()) {
      message.error('MCP 名称不能为空')
      return
    }
    if (!data.transport) {
      message.error('请选择传输类型')
      return
    }
    if (['sse', 'streamable_http'].includes(data.transport)) {
      if (!data.url?.trim()) {
        message.error('HTTP 类型必须填写 MCP URL')
        return
      }
    }
    if (data.transport === 'stdio') {
      if (!data.command?.trim()) {
        message.error('StdIO 类型必须填写命令')
        return
      }
    }

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
