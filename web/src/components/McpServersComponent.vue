<template>
  <div class="mcp-servers extension-page-root">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="layout-wrapper" :class="{ 'content-loading': loading }">
      <!-- 左侧：服务器列表 -->
      <div class="sidebar-list">
        <!-- 搜索框 -->
        <div class="search-box">
          <a-input
            v-model:value="searchQuery"
            placeholder="搜索服务器..."
            allow-clear
            class="search-input"
          >
            <template #prefix><Search :size="14" class="text-muted" /></template>
          </a-input>
        </div>

        <!-- 统计信息 -->
        <!-- <div class="stats-section" v-if="filteredServers.length > 0">
          <span class="stats-text">
            {{ filteredServers.length }} 个服务器： HTTP: {{ httpCount }} · SSE: {{ sseCount }} · StdIO: {{ stdioCount }}
          </span>
        </div> -->

        <!-- 服务器列表 -->
        <div class="list-container">
          <div v-if="filteredServers.length === 0" class="empty-text">
            <a-empty :image="false" :description="searchQuery ? '无匹配服务器' : '暂无服务器'" />
          </div>
          <template v-for="(server, index) in filteredServers" :key="server.name">
            <div
              class="list-item"
              :class="{ active: currentServer?.name === server.name, disabled: !server.enabled }"
              @click="selectServer(server)"
            >
              <div class="item-header">
                <span class="server-icon">{{ server.icon || '🔌' }}</span>
                <span class="item-name">{{ server.name }}</span>
                <a-switch
                  size="small"
                  :checked="server.enabled"
                  @change="handleToggleServer(server)"
                  @click.stop
                  :loading="toggleLoading === server.name"
                />
              </div>
              <div class="item-details">
                <span class="item-desc">{{ server.description || '暂无描述' }}</span>
              </div>
            </div>
            <div v-if="index < filteredServers.length - 1" class="list-separator"></div>
          </template>
        </div>
      </div>

      <!-- 右侧：详情面板 -->
      <div class="main-panel">
        <div v-if="!currentServer" class="unselected-state">
          <div class="hint-box">
            <Plug :size="40" class="text-muted" />
            <p>请在左侧选择服务器进行操作</p>
          </div>
        </div>

        <template v-else>
          <div class="panel-top-bar">
            <h2 class="panel-title-row">
              <span class="server-icon-lg">{{ currentServer.icon || '🔌' }}</span>
              <span
                ><strong>{{ currentServer.name }}</strong></span
              >
            </h2>
            <div class="panel-actions">
              <a-space :size="8">
                <a-button
                  size="small"
                  @click="handleTestServer(currentServer)"
                  :loading="testLoading === currentServer.name"
                  class="lucide-icon-btn"
                >
                  <Zap :size="14" v-if="testLoading !== currentServer.name" />
                  <span>测试</span>
                </a-button>
                <a-button
                  size="small"
                  @click="showEditModal(currentServer)"
                  class="lucide-icon-btn"
                >
                  <Pencil :size="14" />
                  <span>编辑</span>
                </a-button>
                <a-button
                  size="small"
                  danger
                  ghost
                  :disabled="currentServer.created_by === 'system'"
                  @click="confirmDeleteServer(currentServer)"
                  class="lucide-icon-btn"
                >
                  <Trash2 :size="14" />
                  <span>删除</span>
                </a-button>
              </a-space>
            </div>
          </div>

          <!-- Tab 导航 -->
          <a-tabs v-model:activeKey="detailTab" class="detail-tabs">
            <a-tab-pane key="general">
              <template #tab>
                <span class="tab-title"><Settings2 :size="14" />信息</span>
              </template>
              <div class="tab-content">
                <div class="info-grid">
                  <div class="info-item" v-if="currentServer.description">
                    <label>描述</label>
                    <span>{{ currentServer.description }}</span>
                  </div>
                  <div class="info-item">
                    <label>传输类型</label>
                    <span>
                      <a-tag :color="getTransportColor(currentServer.transport)">
                        {{ currentServer.transport }}
                      </a-tag>
                    </span>
                  </div>
                  <div
                    class="info-item"
                    v-if="Array.isArray(currentServer.tags) && currentServer.tags.length > 0"
                  >
                    <label>标签</label>
                    <span>
                      <a-tag v-for="tag in currentServer.tags" :key="tag">{{ tag }}</a-tag>
                    </span>
                  </div>

                  <!-- HTTP 类型显示 URL -->
                  <template
                    v-if="
                      currentServer.transport === 'streamable_http' ||
                      currentServer.transport === 'sse'
                    "
                  >
                    <div class="info-item" v-if="currentServer.url">
                      <label>服务器 URL</label>
                      <span class="code-inline text-break-all">{{ currentServer.url }}</span>
                    </div>
                    <div
                      class="info-item"
                      v-if="currentServer.headers && Object.keys(currentServer.headers).length > 0"
                    >
                      <label>请求头</label>
                      <pre class="code-pre">{{
                        JSON.stringify(currentServer.headers, null, 2)
                      }}</pre>
                    </div>
                    <div class="info-item" v-if="currentServer.timeout">
                      <label>HTTP 超时</label>
                      <span>{{ currentServer.timeout }} 秒</span>
                    </div>
                    <div class="info-item" v-if="currentServer.sse_read_timeout">
                      <label>SSE 读取超时</label>
                      <span>{{ currentServer.sse_read_timeout }} 秒</span>
                    </div>
                  </template>

                  <!-- StdIO 类型显示 command/args -->
                  <template v-if="currentServer.transport === 'stdio'">
                    <div class="info-item" v-if="currentServer.command">
                      <label>命令</label>
                      <span class="code-inline">{{ currentServer.command }}</span>
                    </div>
                    <div
                      class="info-item"
                      v-if="currentServer.args && currentServer.args.length > 0"
                    >
                      <label>参数</label>
                      <span>
                        <a-tag v-for="(arg, index) in currentServer.args" :key="index" size="small">
                          {{ arg }}
                        </a-tag>
                      </span>
                    </div>
                    <div
                      class="info-item"
                      v-if="currentServer.env && Object.keys(currentServer.env).length > 0"
                    >
                      <label>环境变量</label>
                      <pre class="code-pre">{{ JSON.stringify(currentServer.env, null, 2) }}</pre>
                    </div>
                  </template>

                  <div class="info-item">
                    <label>创建时间</label>
                    <span>{{ formatTime(currentServer.created_at) }}</span>
                  </div>
                  <div class="info-item">
                    <label>更新时间</label>
                    <span>{{ formatTime(currentServer.updated_at) }}</span>
                  </div>
                  <div class="info-item">
                    <label>创建人</label>
                    <span>{{ currentServer.created_by }}</span>
                  </div>
                </div>
              </div>
            </a-tab-pane>

            <a-tab-pane key="tools">
              <template #tab>
                <span class="tab-title"><Wrench :size="14" />工具 ({{ tools.length }})</span>
              </template>
              <div class="tab-content tools-tab">
                <div class="tools-toolbar">
                  <a-input-search
                    v-model:value="toolSearchText"
                    placeholder="搜索工具..."
                    style="width: 200px"
                    allowClear
                  />
                  <a-button @click="fetchTools" :loading="toolsLoading" class="lucide-icon-btn">
                    <RotateCw :size="14" />
                    <span>刷新</span>
                  </a-button>
                </div>
                <a-spin :spinning="toolsLoading">
                  <div v-if="filteredTools.length === 0" class="empty-tools">
                    <a-empty :description="toolsError || '暂无工具'" />
                  </div>
                  <div v-else class="tools-list">
                    <div
                      v-for="tool in filteredTools"
                      :key="tool.name"
                      class="tool-card"
                      :class="{ disabled: !tool.enabled }"
                    >
                      <div class="tool-header">
                        <div class="tool-info">
                          <span class="tool-name">{{ tool.name }}</span>
                          <a-tooltip :title="`ID: ${tool.id}`">
                            <Info :size="14" class="info-icon" />
                          </a-tooltip>
                        </div>
                        <div class="tool-actions">
                          <a-switch
                            :checked="tool.enabled"
                            @change="handleToggleTool(tool)"
                            :loading="toggleToolLoading === tool.name"
                            size="small"
                          />
                          <a-tooltip title="复制工具名称">
                            <a-button
                              type="text"
                              size="small"
                              @click="copyToolName(tool.name)"
                              class="lucide-icon-btn"
                            >
                              <Copy :size="14" />
                            </a-button>
                          </a-tooltip>
                        </div>
                      </div>
                      <div class="tool-description" v-if="tool.description">
                        {{ tool.description }}
                      </div>
                      <a-collapse
                        v-if="tool.parameters && Object.keys(tool.parameters).length > 0"
                        ghost
                      >
                        <a-collapse-panel key="params" header="参数">
                          <div class="params-list">
                            <div
                              v-for="(param, paramName) in tool.parameters"
                              :key="paramName"
                              class="param-item"
                            >
                              <div class="param-header">
                                <span class="param-name">{{ paramName }}</span>
                                <span
                                  class="param-required"
                                  v-if="tool.required?.includes(paramName)"
                                  >必填</span
                                >
                                <span class="param-type">{{ param.type || 'any' }}</span>
                              </div>
                              <div class="param-desc" v-if="param.description">
                                {{ param.description }}
                              </div>
                            </div>
                          </div>
                        </a-collapse-panel>
                      </a-collapse>
                    </div>
                  </div>
                </a-spin>
              </div>
            </a-tab-pane>

            <a-tab-pane key="prompts">
              <template #tab>
                <span class="tab-title"><MessageSquare :size="14" />提示</span>
              </template>
              <div class="tab-content empty-tab">
                <a-empty description="提示功能即将推出">
                  <template #image>
                    <span style="font-size: 48px">📝</span>
                  </template>
                </a-empty>
              </div>
            </a-tab-pane>

            <a-tab-pane key="resources">
              <template #tab>
                <span class="tab-title"><Box :size="14" />资源</span>
              </template>
              <div class="tab-content empty-tab">
                <a-empty description="资源功能即将推出">
                  <template #image>
                    <span style="font-size: 48px">📦</span>
                  </template>
                </a-empty>
              </div>
            </a-tab-pane>
          </a-tabs>
        </template>
      </div>
    </div>

    <!-- 添加/编辑服务器模态框 -->
    <a-modal
      v-model:open="formModalVisible"
      :title="editMode ? '编辑 MCP 服务器' : '添加 MCP 服务器'"
      @ok="handleFormSubmit"
      :confirmLoading="formLoading"
      @cancel="formModalVisible = false"
      :maskClosable="false"
      width="560px"
      class="server-modal"
    >
      <!-- 模式切换 -->
      <div class="mode-switch">
        <a-radio-group v-model:value="formMode" button-style="solid" size="small">
          <a-radio-button value="form">表单模式</a-radio-button>
          <a-radio-button value="json">JSON 模式</a-radio-button>
        </a-radio-group>
      </div>

      <!-- 表单模式 -->
      <a-form v-if="formMode === 'form'" layout="vertical" class="extension-form">
        <a-form-item label="服务器名称" required class="form-item">
          <a-input
            v-model:value="form.name"
            placeholder="请输入服务器名称（唯一标识）"
            :disabled="editMode"
          />
        </a-form-item>

        <a-form-item label="描述" class="form-item">
          <a-input v-model:value="form.description" placeholder="请输入服务器描述" />
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

        <!-- HTTP 类型 -->
        <template v-if="form.transport === 'streamable_http' || form.transport === 'sse'">
          <a-form-item label="服务器 URL" required class="form-item">
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

        <!-- StdIO 类型 -->
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

        <a-form-item label="标签" class="form-item">
          <a-select
            v-model:value="form.tags"
            mode="tags"
            placeholder="输入标签后回车添加"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>

      <!-- JSON 模式 -->
      <div v-else class="json-mode">
        <a-textarea
          v-model:value="jsonContent"
          :rows="15"
          placeholder='请输入 JSON 配置，格式如：
{
  "name": "my-server",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "description": "服务器描述",
  "headers": {"Authorization": "Bearer xxx"},
  "tags": ["工具", "AI"]
}'
          class="json-textarea"
        />
        <div class="json-actions">
          <a-button size="small" @click="formatJson">格式化</a-button>
          <a-button size="small" @click="parseJsonToForm">解析到表单</a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { message, notification, Modal } from 'ant-design-vue'
import {
  Search,
  Plug,
  Zap,
  Pencil,
  Trash2,
  RotateCw,
  Info,
  Copy,
  Settings2,
  Wrench,
  MessageSquare,
  Box
} from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import { formatFullDateTime } from '@/utils/time'
import McpEnvEditor from './McpEnvEditor.vue'

// 状态
const loading = ref(false)
const error = ref(null)
const servers = ref([])
const toggleLoading = ref(null)
const testLoading = ref(null)
const searchQuery = ref('')
const currentServer = ref(null)
const detailTab = ref('general')

// 工具相关状态
const tools = ref([])
const toolsLoading = ref(false)
const toolsError = ref(null)
const toolSearchText = ref('')
const toggleToolLoading = ref(null)

// 表单相关
const formModalVisible = ref(false)
const formLoading = ref(false)
const formMode = ref('form')
const editMode = ref(false)
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
  timeout: null,
  sse_read_timeout: null,
  tags: [],
  icon: ''
})

// 计算属性
const filteredServers = computed(() => {
  const sorted = [...servers.value].sort((a, b) => {
    return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', {
      sensitivity: 'base',
      numeric: true
    })
  })
  if (!searchQuery.value) return sorted
  const q = searchQuery.value.toLowerCase()
  return sorted.filter(
    (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  )
})

const isStdioTransport = computed(
  () =>
    String(form.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

// 工具相关计算属性
const filteredTools = computed(() => {
  if (!toolSearchText.value) return tools.value
  const search = toolSearchText.value.toLowerCase()
  return tools.value.filter(
    (t) =>
      t.name.toLowerCase().includes(search) ||
      (t.description && t.description.toLowerCase().includes(search))
  )
})

// 获取服务器列表
const fetchServers = async () => {
  try {
    loading.value = true
    error.value = null
    const result = await mcpApi.getMcpServers()
    if (result.success) {
      servers.value = result.data || []
      // 默认选中排序后的第一个服务器
      if (!currentServer.value && servers.value.length > 0) {
        selectServer(filteredServers.value[0])
      } else if (currentServer.value) {
        const latest = servers.value.find((s) => s.name === currentServer.value.name)
        if (latest) {
          currentServer.value = latest
        }
      }
    } else {
      error.value = result.message || '获取服务器列表失败'
    }
  } catch (err) {
    console.error('获取服务器列表失败:', err)
    error.value = err.message || '获取服务器列表失败'
  } finally {
    loading.value = false
  }
}

// 获取工具列表
const fetchTools = async () => {
  if (!currentServer.value) return

  try {
    toolsLoading.value = true
    toolsError.value = null
    const result = await mcpApi.getMcpServerTools(currentServer.value.name)
    if (result.success) {
      tools.value = result.data || []
    } else {
      toolsError.value = result.message || '获取工具列表失败'
      tools.value = []
    }
  } catch (err) {
    console.error('获取工具列表失败:', err)
    toolsError.value = err.message || '获取工具列表失败'
    tools.value = []
  } finally {
    toolsLoading.value = false
  }
}

// 切换工具启用状态
const handleToggleTool = async (tool) => {
  if (!currentServer.value) return

  try {
    toggleToolLoading.value = tool.name
    const result = await mcpApi.toggleMcpServerTool(currentServer.value.name, tool.name)
    if (result.success) {
      notification.success({ message: result.message })
      const targetTool = tools.value.find((t) => t.name === tool.name)
      if (targetTool) {
        targetTool.enabled = result.enabled
      }
    } else {
      notification.error({ message: result.message || '操作失败' })
    }
  } catch (err) {
    console.error('切换工具状态失败:', err)
    notification.error({ message: err.message || '操作失败' })
  } finally {
    toggleToolLoading.value = null
  }
}

// 复制工具名称
const copyToolName = async (name) => {
  try {
    await navigator.clipboard.writeText(name)
    notification.success({ message: '已复制到剪贴板' })
  } catch {
    notification.error({ message: '复制失败' })
  }
}

// 格式化时间
const formatTime = (timeStr) => formatFullDateTime(timeStr)

// 获取传输类型颜色
const getTransportColor = (transport) => {
  const colors = {
    sse: 'orange',
    stdio: 'green',
    streamable_http: 'blue'
  }
  return colors[transport] || 'blue'
}

// 选择服务器
const selectServer = (server) => {
  currentServer.value = server
  detailTab.value = 'general'
  fetchTools()
}

// 显示添加模态框
const showAddModal = () => {
  editMode.value = false
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
    timeout: null,
    sse_read_timeout: null,
    tags: [],
    icon: ''
  })
  jsonContent.value = ''
  formModalVisible.value = true
}

const applyServerToForm = (server) => {
  editMode.value = true
  formMode.value = 'form'
  Object.assign(form, {
    name: server.name,
    description: server.description || '',
    transport: server.transport,
    url: server.url || '',
    command: server.command || '',
    args: server.args || [],
    env: server.env || null,
    headersText: server.headers ? JSON.stringify(server.headers, null, 2) : '',
    timeout: server.timeout,
    sse_read_timeout: server.sse_read_timeout,
    tags: server.tags || [],
    icon: server.icon || ''
  })
  formModalVisible.value = true
}

// 显示编辑模态框
const showEditModal = async (server) => {
  try {
    const result = await mcpApi.getMcpServer(server.name)
    if (result.success && result.data) {
      applyServerToForm(result.data)
      return
    }
  } catch (err) {
    console.error('获取服务器详情失败，回退使用列表数据:', err)
  }
  applyServerToForm(server)
}

// 处理表单提交
const handleFormSubmit = async () => {
  try {
    formLoading.value = true

    let data
    if (formMode.value === 'json') {
      try {
        data = JSON.parse(jsonContent.value)
      } catch {
        notification.error({ message: 'JSON 格式错误' })
        return
      }
    } else {
      // 解析 headers
      let headers = null
      if (form.headersText.trim()) {
        try {
          headers = JSON.parse(form.headersText)
        } catch {
          notification.error({ message: '请求头 JSON 格式错误' })
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
        timeout: form.timeout || null,
        sse_read_timeout: form.sse_read_timeout || null,
        tags: form.tags.length > 0 ? form.tags : null,
        icon: form.icon || null
      }
    }

    // 校验必填字段
    if (!data.name?.trim()) {
      notification.error({ message: '服务器名称不能为空' })
      return
    }
    if (!data.transport) {
      notification.error({ message: '请选择传输类型' })
      return
    }
    // HTTP 类型校验 URL
    if (['sse', 'streamable_http'].includes(data.transport)) {
      if (!data.url?.trim()) {
        notification.error({ message: 'HTTP 类型必须填写服务器 URL' })
        return
      }
    }
    // StdIO 类型校验 command
    if (data.transport === 'stdio') {
      if (!data.command?.trim()) {
        notification.error({ message: 'StdIO 类型必须填写命令' })
        return
      }
    }

    if (editMode.value) {
      const result = await mcpApi.updateMcpServer(data.name, data)
      if (result.success) {
        notification.success({ message: '服务器更新成功' })
      } else {
        notification.error({ message: result.message || '更新失败' })
        return
      }
    } else {
      const result = await mcpApi.createMcpServer(data)
      if (result.success) {
        notification.success({ message: '服务器创建成功' })
      } else {
        notification.error({ message: result.message || '创建失败' })
        return
      }
    }

    formModalVisible.value = false
    await fetchServers()
  } catch (err) {
    console.error('操作失败:', err)
    notification.error({ message: err.message || '操作失败' })
  } finally {
    formLoading.value = false
  }
}

// 切换服务器启用状态
const handleToggleServer = async (server) => {
  try {
    toggleLoading.value = server.name
    const result = await mcpApi.toggleMcpServer(server.name)
    if (result.success) {
      message.success(result.message)
      await fetchServers()
    } else {
      notification.error({ message: result.message || '操作失败' })
    }
  } catch (err) {
    console.error('切换状态失败:', err)
    notification.error({ message: err.message || '操作失败' })
  } finally {
    toggleLoading.value = null
  }
}

// 测试服务器连接
const handleTestServer = async (server) => {
  try {
    testLoading.value = server.name
    const result = await mcpApi.testMcpServer(server.name)
    if (result.success) {
      notification.success({ message: result.message })
    } else {
      notification.warning({ message: result.message || '连接失败' })
    }
  } catch (err) {
    console.error('测试连接失败:', err)
    notification.error({ message: err.message || '测试失败' })
  } finally {
    testLoading.value = null
  }
}

// 确认删除服务器
const confirmDeleteServer = (server) => {
  // system 创建的服务器不允许删除
  if (server.created_by === 'system') {
    notification.warning({
      message: '无法删除系统服务器',
      description: '系统内置的 MCP 服务器无法删除，如需停用可切换禁用开关。'
    })
    return
  }

  Modal.confirm({
    title: '确认删除服务器',
    content: `确定要删除服务器 "${server.name}" 吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServer(server.name)
        if (result.success) {
          notification.success({ message: '服务器删除成功' })
          await fetchServers()
        } else {
          notification.error({ message: result.message || '删除失败' })
        }
      } catch (err) {
        console.error('删除失败:', err)
        notification.error({ message: err.message || '删除失败' })
      }
    }
  })
}

// 格式化 JSON
const formatJson = () => {
  try {
    const obj = JSON.parse(jsonContent.value)
    jsonContent.value = JSON.stringify(obj, null, 2)
  } catch {
    notification.error({ message: 'JSON 格式错误，无法格式化' })
  }
}

// 解析 JSON 到表单
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
      timeout: obj.timeout || null,
      sse_read_timeout: obj.sse_read_timeout || null,
      tags: obj.tags || [],
      icon: obj.icon || ''
    })
    formMode.value = 'form'
    notification.success({ message: '已解析到表单' })
  } catch {
    notification.error({ message: 'JSON 格式错误' })
  }
}

// 初始化
onMounted(() => {
  fetchServers()
})

// 暴露方法给父组件
defineExpose({
  fetchServers,
  showAddModal
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.stats-section {
  padding: 8px 12px;
  .stats-text {
    font-size: 12px;
    color: var(--gray-500);
  }
}

.list-item {
  .server-icon {
    font-size: 18px;
  }
}

/* 工具列表样式 */
.tools-tab {
  .tools-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }

  .empty-tools {
    padding: 40px 0;
  }

  .tools-list {
    display: flex;
    flex-direction: column;
    gap: 12px;

    .tool-card {
      background: var(--gray-0);
      border: 1px solid var(--gray-150);
      border-radius: 8px;
      padding: 12px 16px;
      transition: all 0.2s ease;

      &:hover {
        border-color: var(--gray-200);
      }

      &.disabled {
        opacity: 0.6;
      }

      .tool-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;

        .tool-info {
          display: flex;
          align-items: center;
          gap: 8px;

          .tool-name {
            font-weight: 600;
            font-size: 14px;
            color: var(--gray-900);
          }

          .info-icon {
            color: var(--gray-400);
            cursor: pointer;
            &:hover {
              color: var(--gray-600);
            }
          }
        }

        .tool-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
      }

      .tool-description {
        font-size: 13px;
        color: var(--gray-600);
        line-height: 1.4;
        margin-bottom: 8px;
      }

      :deep(.ant-collapse) {
        background: transparent;
        border: none;

        .ant-collapse-header {
          padding: 8px 0;
          font-size: 13px;
          color: var(--gray-600);
        }
        .ant-collapse-content-box {
          padding: 0;
        }
      }

      .params-list {
        display: flex;
        flex-direction: column;
        gap: 8px;

        .param-item {
          background: var(--gray-50);
          padding: 8px 12px;
          border-radius: 4px;

          .param-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;

            .param-name {
              font-weight: 500;
              font-size: 13px;
              color: var(--gray-900);
              font-family: @mono-font;
            }
            .param-required {
              font-size: 11px;
              color: var(--color-error-500);
              background: var(--color-error-50);
              padding: 1px 6px;
              border-radius: 3px;
            }
            .param-type {
              font-size: 11px;
              color: var(--gray-500);
              background: var(--gray-100);
              padding: 1px 6px;
              border-radius: 3px;
              font-family: @mono-font;
            }
          }

          .param-desc {
            font-size: 12px;
            color: var(--gray-600);
            line-height: 1.4;
          }
        }
      }
    }
  }
}

/* 模态框样式 */
.server-modal {
  .mode-switch {
    margin-bottom: 16px;
    text-align: right;
  }
  .json-mode {
    .json-textarea {
      font-family: @mono-font;
      font-size: 13px;
    }
    .json-actions {
      margin-top: 12px;
      display: flex;
      gap: 8px;
    }
  }
}
</style>
