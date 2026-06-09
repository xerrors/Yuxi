<template>
  <div class="mcp-detail extension-detail-page">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="detail-top-bar">
      <button class="detail-back-btn" @click="goBack">
        <ArrowLeft :size="16" />
        <span>返回</span>
      </button>
      <div class="detail-title-area">
        <span class="detail-icon">{{ server?.icon || '🔌' }}</span>
        <div class="detail-title-text">
          <h2>{{ server?.name || name }}</h2>
          <span class="detail-subtitle">{{ server?.transport || '' }}</span>
        </div>
      </div>
      <div class="detail-actions">
        <a-space :size="8">
          <button
            type="button"
            @click="handleTestServer"
            :disabled="testLoading"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <Zap :size="14" v-if="!testLoading" />
            <span>测试</span>
          </button>
          <button
            type="button"
            @click="startEdit"
            :disabled="isEditing || !server"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </button>
          <button
            type="button"
            @click="handleDangerAction"
            :class="[
              'lucide-icon-btn',
              'extension-panel-action',
              server?.enabled === false
                ? 'extension-panel-action-primary'
                : 'extension-panel-action-danger'
            ]"
          >
            <Plus v-if="server?.enabled === false" :size="14" />
            <Trash2 v-else :size="14" />
            <span>{{ actionLabel }}</span>
          </button>
        </a-space>
      </div>
    </div>

    <div class="detail-content-wrapper">
      <a-spin :spinning="loading">
        <div v-if="server" class="detail-content-inner">
          <a-tabs v-model:activeKey="detailTab" class="detail-tabs">
            <a-tab-pane key="general">
              <template #tab>
                <span class="tab-title"><Settings2 :size="14" />信息</span>
              </template>
              <div class="tab-content">
                <div v-if="isEditing" class="edit-panel">
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

                  <a-form
                    v-if="formMode === 'form'"
                    layout="vertical"
                    class="extension-form inline-edit-form"
                  >
                    <section class="form-section">
                      <div class="form-section-title">
                        <span>基础信息</span>
                        <small>定义 MCP 的名称、描述与展示方式。</small>
                      </div>
                      <div class="form-grid form-grid-three">
                        <a-form-item label="MCP 名称" required class="form-item">
                          <a-input
                            v-model:value="editForm.name"
                            placeholder="请输入 MCP 名称（唯一标识）"
                            disabled
                          />
                        </a-form-item>
                        <a-form-item label="传输类型" required class="form-item">
                          <a-select v-model:value="editForm.transport">
                            <a-select-option value="streamable_http"
                              >streamable_http</a-select-option
                            >
                            <a-select-option value="sse">sse</a-select-option>
                            <a-select-option value="stdio">stdio</a-select-option>
                          </a-select>
                        </a-form-item>
                        <a-form-item label="图标" class="form-item">
                          <a-input
                            v-model:value="editForm.icon"
                            placeholder="输入 emoji，如 🧠"
                            :maxlength="2"
                          />
                        </a-form-item>
                      </div>
                      <a-form-item label="描述" class="form-item form-item-full">
                        <a-textarea
                          v-model:value="editForm.description"
                          placeholder="请输入 MCP 描述"
                          :rows="2"
                        />
                      </a-form-item>
                    </section>

                    <section class="form-section">
                      <div class="form-section-title">
                        <span>连接配置</span>
                        <small>配置当前传输方式需要的连接参数。</small>
                      </div>
                      <template
                        v-if="
                          editForm.transport === 'streamable_http' || editForm.transport === 'sse'
                        "
                      >
                        <a-form-item label="MCP URL" required class="form-item form-item-full">
                          <a-input
                            v-model:value="editForm.url"
                            placeholder="https://example.com/mcp"
                          />
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
                          <a-input
                            v-model:value="editForm.command"
                            placeholder="例如：npx 或 /path/to/server"
                          />
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
                      <template
                        v-if="
                          editForm.transport === 'streamable_http' || editForm.transport === 'sse'
                        "
                      >
                        <a-form-item label="HTTP 请求头" class="form-item form-item-full">
                          <a-textarea
                            v-model:value="editForm.headersText"
                            placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
                            :rows="4"
                            class="config-textarea"
                          />
                          <div class="form-helper">
                            请输入合法 JSON 对象，留空表示不发送额外请求头。
                          </div>
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
                      <McpAuthConfigBuilder
                        v-model="editForm.authConfigText"
                        :transport="editForm.transport"
                      />
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
                    <a-button @click="cancelEdit" :disabled="editLoading" class="lucide-icon-btn">
                      <template #icon><X :size="14" /></template>
                      取消
                    </a-button>
                    <a-button
                      type="primary"
                      @click="handleSaveEdit"
                      :loading="editLoading"
                      class="lucide-icon-btn"
                    >
                      <template #icon><Save :size="14" /></template>
                      保存
                    </a-button>
                  </div>
                </div>

                <div v-else class="info-grid">
                  <div class="info-item" v-if="server.description">
                    <label>描述</label>
                    <span>{{ server.description }}</span>
                  </div>
                  <div class="info-item">
                    <label>传输类型</label>
                    <span>
                      <a-tag :color="getTransportColor(server.transport)">{{
                        server.transport
                      }}</a-tag>
                    </span>
                  </div>
                  <div
                    class="info-item"
                    v-if="Array.isArray(server.tags) && server.tags.length > 0"
                  >
                    <label>标签</label>
                    <span>
                      <a-tag v-for="tag in server.tags" :key="tag">{{ tag }}</a-tag>
                    </span>
                  </div>
                  <template
                    v-if="server.transport === 'streamable_http' || server.transport === 'sse'"
                  >
                    <div class="info-item" v-if="server.url">
                      <label>MCP URL</label>
                      <span class="code-inline text-break-all">{{ server.url }}</span>
                    </div>
                    <div
                      class="info-item"
                      v-if="server.headers && Object.keys(server.headers).length > 0"
                    >
                      <label>请求头</label>
                      <pre class="code-pre">{{ JSON.stringify(server.headers, null, 2) }}</pre>
                    </div>
                    <div class="info-item" v-if="server.timeout">
                      <label>HTTP 超时</label>
                      <span>{{ server.timeout }} 秒</span>
                    </div>
                    <div class="info-item" v-if="server.sse_read_timeout">
                      <label>SSE 读取超时</label>
                      <span>{{ server.sse_read_timeout }} 秒</span>
                    </div>
                  </template>
                  <template v-if="server.transport === 'stdio'">
                    <div class="info-item" v-if="server.command">
                      <label>命令</label>
                      <span class="code-inline">{{ server.command }}</span>
                    </div>
                    <div class="info-item" v-if="server.args && server.args.length > 0">
                      <label>参数</label>
                      <span>
                        <a-tag v-for="(arg, index) in server.args" :key="index" size="small">{{
                          arg
                        }}</a-tag>
                      </span>
                    </div>
                    <div class="info-item" v-if="server.env && Object.keys(server.env).length > 0">
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
                    class="info-item info-item-full"
                    v-if="server.auth_config && Object.keys(server.auth_config).length > 0"
                    style="grid-column: 1 / -1; margin-top: 10px"
                  >
                    <label style="margin-bottom: 8px; display: block">认证配置明细</label>
                    <McpAuthConfigBuilder
                      :modelValue="JSON.stringify(server.auth_config, null, 2)"
                      :transport="server.transport"
                      readonly
                    />
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

            <a-tab-pane key="connections">
              <template #tab>
                <span class="tab-title"
                  ><KeyRound :size="14" />连接 ({{ connectionTotalCount }})</span
                >
              </template>
              <div class="tab-content connections-tab">
                <div class="connection-command-bar">
                  <div class="connection-command-copy">
                    <h3>连接管理</h3>
                    <p>
                      {{
                        hasAuthConfig
                          ? '按全局、部门或用户维护长期凭据，运行时自动换取和刷新 token。'
                          : '当前 MCP 未配置动态鉴权，通常不需要维护连接。'
                      }}
                    </p>
                  </div>
                  <a-space :size="8">
                    <a-tooltip
                      :title="hasAuthConfig ? '创建新的鉴权连接' : '请先在 MCP 编辑页配置认证策略'"
                    >
                      <a-button
                        type="primary"
                        @click="openCreateConnectionDrawer"
                        :disabled="!hasAuthConfig"
                        class="lucide-icon-btn"
                      >
                        <Plus :size="14" />
                        <span>新建连接</span>
                      </a-button>
                    </a-tooltip>
                    <a-button
                      @click="fetchConnections"
                      :loading="connectionsLoading"
                      class="lucide-icon-btn"
                    >
                      <RotateCw :size="14" />
                      <span>刷新</span>
                    </a-button>
                  </a-space>
                </div>

                <div class="connection-summary-strip">
                  <div class="connection-summary-item">
                    <span class="summary-label">认证方式</span>
                    <strong>{{
                      providerLabelMap[server.auth_config?.provider] ||
                      server.auth_config?.provider ||
                      '未配置'
                    }}</strong>
                  </div>
                  <div class="connection-summary-item">
                    <span class="summary-label">默认绑定</span>
                    <strong>{{ authBindingScopeLabel }}</strong>
                  </div>
                  <div class="connection-summary-item">
                    <span class="summary-label">可用连接</span>
                    <strong>{{ activeConnectionCount }}</strong>
                  </div>
                  <div class="connection-summary-item">
                    <span class="summary-label">需处理</span>
                    <strong>{{ attentionConnectionCount }}</strong>
                  </div>
                </div>

                <div class="connection-list-toolbar">
                  <div class="connection-filter-group">
                    <a-segmented
                      v-model:value="connectionFilter"
                      :options="connectionFilterOptions"
                    />
                    <a-input
                      v-model:value="connectionSearchText"
                      allow-clear
                      class="connection-search-input"
                      placeholder="搜索连接名、绑定对象"
                    >
                      <template #prefix>
                        <Search :size="14" />
                      </template>
                    </a-input>
                  </div>
                  <div class="connection-page-controls">
                    <span class="connection-result-count">共 {{ connectionTotal }} 条</span>
                    <a-pagination
                      v-if="connectionTotal > connectionPageSize"
                      v-model:current="connectionPage"
                      v-model:page-size="connectionPageSize"
                      size="small"
                      :total="connectionTotal"
                      :page-size-options="['12', '24', '48']"
                      show-size-changer
                      show-less-items
                    />
                  </div>
                </div>

                <a-spin :spinning="connectionsLoading">
                  <div v-if="connectionsError" class="detail-empty">
                    <a-empty :description="connectionsError" />
                  </div>
                  <div v-else-if="connections.length === 0" class="connection-empty-state">
                    <a-empty :description="connectionEmptyDescription" />
                    <a-button
                      v-if="hasAuthConfig && !hasConnectionListFilter"
                      type="primary"
                      @click="openCreateConnectionDrawer"
                      class="lucide-icon-btn"
                    >
                      <Plus :size="14" />
                      <span>新建连接</span>
                    </a-button>
                    <a-button v-else-if="hasConnectionListFilter" @click="resetConnectionFilters">
                      清除筛选
                    </a-button>
                  </div>
                  <div v-else class="connection-list-body">
                    <div class="connection-cards-grid">
                      <div
                        v-for="connection in connections"
                        :key="connection.id"
                        class="connection-card"
                      >
                        <div class="connection-card-header">
                          <div class="connection-key-info">
                            <KeyRound :size="18" class="connection-key-icon" />
                            <div class="connection-key-copy">
                              <h4>{{ getConnectionTitle(connection) }}</h4>
                              <span v-if="connection.external_subject">
                                {{ connection.external_subject }}
                              </span>
                            </div>
                          </div>
                          <span
                            class="connection-scope-badge"
                            :class="[
                              `scope-${connection.scope_type || 'unknown'}`,
                              { 'is-mismatch': !isConnectionScopeMatched(connection) }
                            ]"
                            :title="`生效范围：${getConnectionScopeLabel(connection.scope_type)}`"
                          >
                            <Globe2 v-if="connection.scope_type === 'system'" :size="13" />
                            <Building2
                              v-else-if="connection.scope_type === 'department'"
                              :size="13"
                            />
                            <UserRound v-else :size="13" />
                            {{ getConnectionScopeLabel(connection.scope_type) }}
                          </span>
                        </div>

                        <div class="connection-card-content">
                          <div
                            v-if="getConnectionIssue(connection)"
                            class="connection-issue"
                            :class="`issue-${getConnectionIssue(connection).tone}`"
                          >
                            <div class="issue-copy">
                              <span>问题：{{ getConnectionIssue(connection).label }}</span>
                              <small>{{ getConnectionIssue(connection).description }}</small>
                            </div>
                            <a-button
                              type="link"
                              size="small"
                              class="issue-action"
                              :loading="
                                connectionActionLoading ===
                                `${connection.id}:${getConnectionIssue(connection).key === 'reauth_required' ? 'reauth' : 'issue'}`
                              "
                              @click="handleConnectionIssueAction(connection)"
                            >
                              {{ getConnectionIssue(connection).actionLabel }}
                            </a-button>
                          </div>
                          <div class="connection-info-item">
                            <span class="info-label">绑定对象:</span>
                            <span class="info-value">{{
                              getConnectionScopeTargetLabel(connection)
                            }}</span>
                          </div>
                          <div class="connection-info-item">
                            <span class="info-label">最近记录:</span>
                            <span class="info-value">{{ getConnectionLastInfo(connection) }}</span>
                          </div>
                        </div>

                        <div class="connection-card-footer">
                          <div class="footer-left">
                            <span class="switch-label">
                              {{ getConnectionStatusSwitchLabel(connection) }}
                            </span>
                            <a-tooltip :title="getConnectionStatusToggleTooltip(connection)">
                              <span class="status-switch-wrap">
                                <a-switch
                                  size="small"
                                  :checked="connection.status === 'active'"
                                  :disabled="!canToggleConnectionStatus(connection)"
                                  :loading="connectionActionLoading === `${connection.id}:status`"
                                  @change="
                                    (checked) => handleToggleConnectionStatus(connection, checked)
                                  "
                                />
                              </span>
                            </a-tooltip>
                          </div>
                          <div class="connection-row-actions">
                            <a-button
                              type="text"
                              size="small"
                              class="connection-action-btn lucide-icon-btn"
                              @click="startEditConnection(connection)"
                            >
                              <Pencil :size="14" />
                              <span>编辑</span>
                            </a-button>
                            <a-tooltip :title="getConnectionTestTooltip(connection)">
                              <span class="connection-action-wrap">
                                <a-button
                                  type="text"
                                  size="small"
                                  class="connection-action-btn lucide-icon-btn"
                                  :disabled="!canTestConnection(connection)"
                                  @click="handleTestConnection(connection)"
                                  :loading="connectionActionLoading === `${connection.id}:test`"
                                >
                                  <Zap :size="14" />
                                  <span>测试</span>
                                </a-button>
                              </span>
                            </a-tooltip>
                            <a-tooltip :title="getConnectionReauthorizeTooltip(connection)">
                              <span class="connection-action-wrap">
                                <a-button
                                  type="text"
                                  size="small"
                                  class="connection-action-btn lucide-icon-btn"
                                  :disabled="!canReauthorizeConnection(connection)"
                                  @click="handleReauthorizeConnection(connection)"
                                  :loading="connectionActionLoading === `${connection.id}:reauth`"
                                >
                                  <RotateCw :size="14" />
                                  <span>重连</span>
                                </a-button>
                              </span>
                            </a-tooltip>
                            <a-button
                              type="text"
                              size="small"
                              danger
                              class="connection-action-btn danger-action-btn lucide-icon-btn"
                              @click="handleDeleteConnection(connection)"
                            >
                              <Trash2 :size="14" />
                              <span>删除</span>
                            </a-button>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div v-if="connectionTotal > connectionPageSize" class="connection-pagination">
                      <a-pagination
                        v-model:current="connectionPage"
                        v-model:page-size="connectionPageSize"
                        :total="connectionTotal"
                        :page-size-options="['12', '24', '48']"
                        show-size-changer
                        show-less-items
                      />
                    </div>
                  </div>
                </a-spin>

                <a-drawer
                  v-model:open="showConnectionForm"
                  :title="connectionDrawerTitle"
                  placement="right"
                  width="min(560px, calc(100vw - 24px))"
                  :body-style="{ padding: 0 }"
                  destroy-on-close
                  class="mcp-connection-drawer"
                  @close="closeConnectionForm"
                >
                  <a-form layout="vertical" class="connection-drawer-form">
                    <section class="drawer-section">
                      <div class="drawer-section-title">
                        <span>绑定范围</span>
                        <small>决定运行时为哪些请求使用这组凭据。</small>
                      </div>
                      <div class="scope-option-grid">
                        <button
                          v-for="option in availableConnectionScopeOptions"
                          :key="option.value"
                          type="button"
                          class="scope-option"
                          :class="{ active: connectionForm.scopeType === option.value }"
                          :disabled="isEditingConnection"
                          @click="connectionForm.scopeType = option.value"
                        >
                          <component :is="option.icon" :size="16" />
                          <span>{{ option.label }}</span>
                          <small>{{ option.description }}</small>
                        </button>
                      </div>
                      <a-form-item
                        v-if="showScopeIdField"
                        :label="scopeIdLabel"
                        required
                        class="form-item"
                      >
                        <a-select
                          v-if="connectionForm.scopeType === 'department'"
                          v-model:value="connectionForm.scopeId"
                          :disabled="isEditingConnection"
                          :loading="isFetchingScopeOptions"
                          placeholder="请选择部门"
                          show-search
                          :options="
                            departmentList.map((d) => ({ label: d.name, value: d.id.toString() }))
                          "
                        />
                        <a-select
                          v-else-if="connectionForm.scopeType === 'user'"
                          v-model:value="connectionForm.scopeId"
                          :disabled="isEditingConnection"
                          :loading="isFetchingScopeOptions"
                          placeholder="请选择用户"
                          show-search
                          :options="
                            userList.map((u) => ({
                              label:
                                u.username === u.user_id
                                  ? u.username
                                  : `${u.username} (${u.user_id})`,
                              value: u.id.toString()
                            }))
                          "
                        />
                        <a-input
                          v-else
                          v-model:value="connectionForm.scopeId"
                          :disabled="isEditingConnection"
                          :placeholder="scopeIdPlaceholder"
                        />
                      </a-form-item>
                    </section>

                    <section class="drawer-section">
                      <div class="drawer-section-title">
                        <span>展示信息</span>
                        <small>名称用于列表识别，不参与鉴权计算。</small>
                      </div>
                      <a-form-item label="连接名称" class="form-item">
                        <a-input
                          v-model:value="connectionForm.displayName"
                          placeholder="例如：财务部共享连接"
                        />
                      </a-form-item>
                    </section>

                    <section class="drawer-section">
                      <div class="drawer-section-title">
                        <span>凭据</span>
                        <small>{{ credentialHint }}</small>
                      </div>
                      <div v-if="credentialSecretFields.length > 0" class="secret-field-grid">
                        <a-form-item
                          v-for="fieldName in credentialSecretFields"
                          :key="fieldName"
                          :label="getSecretFieldLabel(fieldName)"
                          class="form-item"
                        >
                          <a-input-password
                            v-model:value="connectionForm.secretValues[fieldName]"
                            :placeholder="
                              isEditingConnection ? '留空表示保持现有值' : `请输入 ${fieldName}`
                            "
                          />
                        </a-form-item>
                      </div>
                      <a-form-item v-else label="长期凭据" class="form-item">
                        <a-textarea
                          v-model:value="connectionForm.credentialText"
                          :rows="4"
                          class="config-textarea"
                          :placeholder="
                            isEditingConnection
                              ? '留空表示保持现有凭据'
                              : '粘贴长期 token；复杂场景可在高级设置中填写 JSON'
                          "
                        />
                      </a-form-item>
                    </section>

                    <a-collapse ghost class="connection-advanced-collapse">
                      <a-collapse-panel key="advanced" header="高级设置">
                        <a-form-item label="外部主体标识" class="form-item">
                          <a-input
                            v-model:value="connectionForm.externalSubject"
                            placeholder="可选，例如外部用户名或 tenant subject"
                          />
                        </a-form-item>
                        <a-form-item
                          v-if="credentialSecretFields.length > 0"
                          label="原始凭据 JSON"
                          class="form-item"
                        >
                          <a-textarea
                            v-model:value="connectionForm.credentialText"
                            :rows="5"
                            class="config-textarea"
                            placeholder='可选。填写后会覆盖上方密钥字段，例如 {"secrets":{"client_id":"xxx"}}'
                          />
                        </a-form-item>
                        <a-form-item label="元数据 JSON" class="form-item">
                          <a-textarea
                            v-model:value="connectionForm.metaText"
                            :rows="4"
                            class="config-textarea"
                            placeholder='可选，例如 {"tenant":"finance"}'
                          />
                        </a-form-item>
                      </a-collapse-panel>
                    </a-collapse>

                    <div class="connection-drawer-footer">
                      <a-button @click="closeConnectionForm" :disabled="connectionSubmitting">
                        取消
                      </a-button>
                      <a-button
                        type="primary"
                        :loading="connectionSubmitting"
                        @click="handleSubmitConnection"
                      >
                        {{ isEditingConnection ? '保存连接' : '创建连接' }}
                      </a-button>
                    </div>
                  </a-form>
                </a-drawer>
              </div>
            </a-tab-pane>
          </a-tabs>
        </div>
        <div v-else-if="!loading" class="detail-empty">
          <a-empty description="未找到 MCP 服务器" />
        </div>
      </a-spin>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  Zap,
  Pencil,
  Trash2,
  Plus,
  RotateCw,
  Info,
  Copy,
  Settings2,
  Wrench,
  Save,
  X,
  Rows3,
  Braces,
  KeyRound,
  Globe2,
  Building2,
  UserRound,
  Search
} from 'lucide-vue-next'
import { mcpApi } from '@/apis/mcp_api'
import { formatFullDateTime } from '@/utils/time'
import { extractSecretFieldNames } from '@/utils/mcpAuthConfigBuilder'
import McpAuthConfigBuilder from '@/components/extensions/McpAuthConfigBuilder.vue'
import McpEnvEditor from '@/components/McpEnvEditor.vue'
import { departmentApi } from '@/apis/department_api'
import { userApi } from '@/apis/user_api'

const route = useRoute()
const router = useRouter()
const name = computed(() => decodeURIComponent(route.params.name))

const loading = ref(false)
const server = ref(null)
const detailTab = ref('general')
const testLoading = ref(null)

const userList = ref([])
const departmentList = ref([])
const isFetchingScopeOptions = ref(false)

const tools = ref([])
const toolsLoading = ref(false)
const toolsError = ref(null)
const toolSearchText = ref('')
const toggleToolLoading = ref(null)
const connections = ref([])
const connectionsLoading = ref(false)
const connectionsError = ref(null)
const connectionFilter = ref('all')
const connectionSearchText = ref('')
const connectionPage = ref(1)
const connectionPageSize = ref(12)
const connectionTotal = ref(0)
const connectionSummary = reactive({
  total: 0,
  active: 0,
  attention: 0,
  disabled: 0
})
let connectionSearchTimer = null
const showConnectionForm = ref(false)
const connectionSubmitting = ref(false)
const connectionActionLoading = ref(null)
const editingConnectionId = ref(null)

const isEditing = ref(false)
const editLoading = ref(false)
const formMode = ref('form')
const jsonContent = ref('')

const editForm = reactive({
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

const connectionForm = reactive({
  scopeType: 'department',
  scopeId: '',
  displayName: '',
  externalSubject: '',
  credentialText: '',
  secretValues: {},
  metaText: ''
})

const connectionScopeOptions = [
  {
    value: 'system',
    label: '全局共享',
    description: '所有用户共用',
    icon: Globe2
  },
  {
    value: 'department',
    label: '部门共享',
    description: '按部门隔离',
    icon: Building2
  },
  {
    value: 'user',
    label: '个人专用',
    description: '按用户隔离',
    icon: UserRound
  }
]

const scopeLabelMap = {
  inline: '内联',
  system: '全局共享',
  department: '部门共享',
  user: '个人专用'
}

const statusLabelMap = {
  active: '启用',
  disabled: '停用',
  reauth_required: '需要重连',
  invalid: '无效'
}

const providerLabelMap = {
  none: '不启用',
  bound_secret: '绑定长期密钥',
  custom_http_token: '接口换 Token',
  stdio_env: 'StdIO 环境变量',
  client_credentials: 'OAuth2 客户端凭证'
}

const connectionFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '生效中', value: 'active' },
  { label: '需处理', value: 'attention' },
  { label: '未启用', value: 'disabled' }
]

const actionLabel = computed(() => {
  if (server.value?.enabled === false) return '恢复'
  return server.value?.created_by === 'system' ? '移除' : '退役'
})

const isEditingConnection = computed(() => editingConnectionId.value !== null)

const hasAuthConfig = computed(
  () => !!server.value?.auth_config && Object.keys(server.value.auth_config).length > 0
)

const validConnectionScopeTypes = ['system', 'department', 'user']

const effectiveConnectionScopeType = computed(() => {
  const bindingScope = server.value?.auth_config?.binding_scope
  return validConnectionScopeTypes.includes(bindingScope) ? bindingScope : ''
})

const availableConnectionScopeOptions = computed(() => {
  if (!effectiveConnectionScopeType.value) return connectionScopeOptions
  return connectionScopeOptions.filter(
    (option) => option.value === effectiveConnectionScopeType.value
  )
})

const authBindingScopeLabel = computed(() => {
  const bindingScope = server.value?.auth_config?.binding_scope
  return scopeLabelMap[bindingScope] || '未限定'
})

const connectionTotalCount = computed(() => connectionSummary.total || connectionTotal.value)

const activeConnectionCount = computed(() => connectionSummary.active || 0)

const attentionConnectionCount = computed(() => connectionSummary.attention || 0)

const hasConnectionListFilter = computed(
  () => connectionFilter.value !== 'all' || Boolean(connectionSearchText.value.trim())
)

const connectionEmptyDescription = computed(() => {
  if (hasConnectionListFilter.value) return '没有匹配的连接。'
  if (hasAuthConfig.value) return '暂无连接。创建连接后，运行时会按绑定范围自动选择凭据。'
  return '当前 MCP 没有启用动态鉴权连接。'
})

const connectionDrawerTitle = computed(() => (isEditingConnection.value ? '编辑连接' : '新建连接'))

const filteredTools = computed(() => {
  if (!toolSearchText.value) return tools.value
  const search = toolSearchText.value.toLowerCase()
  return tools.value.filter(
    (t) =>
      t.name.toLowerCase().includes(search) ||
      (t.description && t.description.toLowerCase().includes(search))
  )
})

const isStdioTransport = computed(
  () =>
    String(editForm.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

const credentialSecretFields = computed(() =>
  extractSecretFieldNames(server.value?.auth_config || {})
)

const connectionCredentialsRequired = computed(() => credentialSecretFields.value.length > 0)

const showScopeIdField = computed(() => connectionForm.scopeType !== 'system')

const scopeIdLabel = computed(() => {
  if (connectionForm.scopeType === 'department') return '部门'
  if (connectionForm.scopeType === 'user') return '用户'
  return '范围标识'
})

const scopeIdPlaceholder = computed(() => {
  if (connectionForm.scopeType === 'department') return '请选择部门'
  if (connectionForm.scopeType === 'user') return '请选择用户'
  return '留空默认 global'
})

const credentialHint = computed(() => {
  if (isEditingConnection.value) {
    return '为安全起见不回显已有凭据；留空表示保持原值。'
  }
  if (credentialSecretFields.value.length > 0) {
    return '系统已根据认证配置推导出需要录入的密钥字段。'
  }
  return '当前认证配置没有声明密钥字段，可直接粘贴长期 token。'
})

const goBack = () => {
  router.push({ path: '/extensions', query: { tab: 'mcp' } })
}

const formatTime = (timeStr) => formatFullDateTime(timeStr)

const getTransportColor = (transport) => {
  const colors = { sse: 'orange', stdio: 'green', streamable_http: 'blue' }
  return colors[transport] || 'blue'
}

const createEmptySecretValues = () =>
  Object.fromEntries(credentialSecretFields.value.map((fieldName) => [fieldName, '']))

const setNestedSecretValue = (target, path, value) => {
  const segments = String(path || '')
    .split('.')
    .filter(Boolean)
  let current = target
  segments.forEach((segment, index) => {
    if (index === segments.length - 1) {
      current[segment] = value
      return
    }
    current[segment] = current[segment] || {}
    current = current[segment]
  })
}

const getConnectionTitle = (connection) =>
  connection.display_name ||
  `${getConnectionScopeLabel(connection.scope_type)} ${getConnectionScopeTargetLabel(connection)}`

const getConnectionScopeLabel = (scopeType) => scopeLabelMap[scopeType] || scopeType || '未知范围'

const getConnectionStatusLabel = (status) => statusLabelMap[status] || status || '未知状态'

const isConnectionScopeMatched = (connection) =>
  !effectiveConnectionScopeType.value ||
  connection?.scope_type === effectiveConnectionScopeType.value

const isConnectionCredentialMissing = (connection) =>
  connectionCredentialsRequired.value && !connection?.has_credentials

const getConnectionScopeTargetLabel = (connection) => {
  const scopeId = String(connection?.scope_id || '')
  if (connection?.scope_type === 'system') {
    return '全部用户'
  }
  if (connection?.scope_type === 'department') {
    const department = departmentList.value.find((item) => String(item.id) === scopeId)
    return department ? `${department.name} (#${department.id})` : `部门 #${scopeId || '-'}`
  }
  if (connection?.scope_type === 'user') {
    const user = userList.value.find(
      (item) => String(item.id) === scopeId || String(item.user_id) === scopeId
    )
    if (!user) return `用户 #${scopeId || '-'}`
    return user.username === user.user_id ? user.username : `${user.username} (${user.user_id})`
  }
  return scopeId || '未指定'
}

const canToggleConnectionStatus = (connection) => {
  if (!['active', 'disabled'].includes(connection?.status)) return false
  if (connection.status === 'disabled') {
    return isConnectionScopeMatched(connection) && !isConnectionCredentialMissing(connection)
  }
  return true
}

const getConnectionStatusSwitchLabel = (connection) => {
  if (connection?.status === 'active') return '已启用'
  return getConnectionStatusLabel(connection?.status)
}

const getConnectionStatusToggleTooltip = (connection) => {
  if (connection?.status === 'active') return '停用连接'
  if (connection?.status === 'disabled') {
    if (!isConnectionScopeMatched(connection)) {
      return `该连接未生效，不能启用；当前 MCP 使用${authBindingScopeLabel.value}`
    }
    if (isConnectionCredentialMissing(connection)) {
      return '请先补充凭据'
    }
    return '启用连接'
  }
  return '请先重连或编辑凭据'
}

const canTestConnection = (connection) =>
  isConnectionScopeMatched(connection) && !isConnectionCredentialMissing(connection)

const getConnectionTestTooltip = (connection) => {
  if (canTestConnection(connection)) return '测试连接'
  if (isConnectionCredentialMissing(connection)) return '请先补充凭据'
  return `该连接未生效，当前 MCP 使用${authBindingScopeLabel.value}`
}

const canReauthorizeConnection = (connection) =>
  isConnectionScopeMatched(connection) && !isConnectionCredentialMissing(connection)

const getConnectionReauthorizeTooltip = (connection) => {
  if (canReauthorizeConnection(connection)) return '重置授权并重新激活'
  if (isConnectionCredentialMissing(connection)) return '请先补充凭据'
  return `该连接未生效，不能重连；当前 MCP 使用${authBindingScopeLabel.value}`
}

const getConnectionIssue = (connection) => {
  if (!isConnectionScopeMatched(connection)) {
    return {
      key: 'scope_mismatch',
      label: '范围不匹配',
      description: `当前 MCP 使用${authBindingScopeLabel.value}，这组连接不会在运行时生效。`,
      actionLabel: '新建匹配连接',
      tone: 'warning'
    }
  }
  if (isConnectionCredentialMissing(connection)) {
    return {
      key: 'missing_credentials',
      label: '缺少凭据',
      description: '缺少长期凭据，运行时无法换取或注入 token。',
      actionLabel: '补充凭据',
      tone: 'error'
    }
  }
  if (connection?.status === 'reauth_required') {
    return {
      key: 'reauth_required',
      label: '授权失效',
      description: '缓存 token 已失效，需要重新授权后才能继续使用。',
      actionLabel: '重连',
      tone: 'warning'
    }
  }
  if (connection?.status === 'invalid' || connection?.meta_json?.last_error?.message) {
    return {
      key: 'test_failed',
      label: '测试失败',
      description: connection?.meta_json?.last_error?.message || '最近一次连接检测失败。',
      actionLabel: '编辑凭据',
      tone: 'error'
    }
  }
  return null
}

const getConnectionLastInfo = (connection) => {
  if (connection.meta_json?.last_success_at) {
    return `最近成功 ${formatTime(connection.meta_json.last_success_at)}`
  }
  if (connection.updated_at) {
    return `更新于 ${formatTime(connection.updated_at)}`
  }
  return '暂无记录'
}

const getSecretFieldLabel = (fieldName) => {
  const labelMap = {
    client_id: 'Client ID',
    client_secret: 'Client Secret',
    access_token: 'Access Token',
    refresh_token: 'Refresh Token',
    issuer_url: 'Issuer URL'
  }
  return labelMap[fieldName] || fieldName
}

const resetEditForm = (data) => {
  Object.assign(editForm, {
    name: data?.name || '',
    description: data?.description || '',
    transport: data?.transport || 'streamable_http',
    url: data?.url || '',
    command: data?.command || '',
    args: data?.args || [],
    env: data?.env || null,
    headersText: data?.headers ? JSON.stringify(data.headers, null, 2) : '',
    authConfigText: data?.auth_config ? JSON.stringify(data.auth_config, null, 2) : '',
    timeout: data?.timeout,
    sse_read_timeout: data?.sse_read_timeout,
    tags: data?.tags || [],
    icon: data?.icon || ''
  })
  jsonContent.value = data ? JSON.stringify(data, null, 2) : ''
}

const startEdit = () => {
  if (!server.value) return
  detailTab.value = 'general'
  formMode.value = 'form'
  resetEditForm(server.value)
  isEditing.value = true
}

const cancelEdit = () => {
  isEditing.value = false
  resetEditForm(server.value)
}

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
    resetEditForm(obj)
    formMode.value = 'form'
    message.success('已解析到表单')
  } catch {
    message.error('JSON 格式错误')
  }
}

const parseJsonText = (text, label, { allowRawString = false } = {}) => {
  const trimmed = String(text || '').trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    if (allowRawString) {
      return trimmed
    }
    message.error(`${label} JSON 格式错误`)
    return undefined
  }
}

const buildEditPayload = () => {
  if (formMode.value === 'json') {
    try {
      return JSON.parse(jsonContent.value)
    } catch {
      message.error('JSON 格式错误')
      return null
    }
  }

  let headers = null
  if (editForm.headersText.trim()) {
    try {
      headers = JSON.parse(editForm.headersText)
    } catch {
      message.error('请求头 JSON 格式错误')
      return null
    }
  }

  const authConfig = parseJsonText(editForm.authConfigText, '认证配置')
  if (authConfig === undefined) {
    return null
  }

  return {
    name: editForm.name,
    description: editForm.description || null,
    transport: editForm.transport,
    url: editForm.url || null,
    command: editForm.command || null,
    args: editForm.args.length > 0 ? editForm.args : null,
    env: editForm.env,
    headers,
    auth_config: authConfig,
    timeout: editForm.timeout || null,
    sse_read_timeout: editForm.sse_read_timeout || null,
    tags: editForm.tags.length > 0 ? editForm.tags : null,
    icon: editForm.icon || null
  }
}

const getDefaultConnectionScopeType = () =>
  availableConnectionScopeOptions.value[0]?.value || 'department'

const resetConnectionForm = () => {
  editingConnectionId.value = null
  Object.assign(connectionForm, {
    scopeType: getDefaultConnectionScopeType(),
    scopeId: '',
    displayName: '',
    externalSubject: '',
    credentialText: '',
    secretValues: createEmptySecretValues(),
    metaText: ''
  })
}

const openCreateConnectionDrawer = () => {
  resetConnectionForm()
  showConnectionForm.value = true
}

const closeConnectionForm = () => {
  showConnectionForm.value = false
  resetConnectionForm()
}

const startEditConnection = (connection) => {
  editingConnectionId.value = connection.id
  showConnectionForm.value = true
  Object.assign(connectionForm, {
    scopeType: connection.scope_type || 'department',
    scopeId: connection.scope_id || '',
    displayName: connection.display_name || '',
    externalSubject: connection.external_subject || '',
    credentialText: '',
    secretValues: createEmptySecretValues(),
    metaText: connection.meta_json ? JSON.stringify(connection.meta_json, null, 2) : ''
  })
}

const validateEditPayload = (data) => {
  if (!data.name?.trim()) {
    message.error('MCP 名称不能为空')
    return false
  }
  if (!data.transport) {
    message.error('请选择传输类型')
    return false
  }
  if (['sse', 'streamable_http'].includes(data.transport) && !data.url?.trim()) {
    message.error('HTTP 类型必须填写 MCP URL')
    return false
  }
  if (data.transport === 'stdio' && !data.command?.trim()) {
    message.error('StdIO 类型必须填写命令')
    return false
  }
  return true
}

const handleSaveEdit = async () => {
  if (!server.value) return
  const data = buildEditPayload()
  if (!data || !validateEditPayload(data)) return

  try {
    editLoading.value = true
    const result = await mcpApi.updateMcpServer(server.value.name, data)
    if (result.success) {
      message.success('MCP 更新成功')
      isEditing.value = false
      await fetchServer()
    } else {
      message.error(result.message || '更新失败')
    }
  } catch (err) {
    message.error(err.message || '更新失败')
  } finally {
    editLoading.value = false
  }
}

const fetchServer = async () => {
  try {
    loading.value = true
    const result = await mcpApi.getMcpServer(name.value)
    if (result.success) {
      server.value = result.data
    } else {
      message.error(result.message || '获取 MCP 详情失败')
    }
  } catch (err) {
    message.error(err.message || '获取 MCP 详情失败')
  } finally {
    loading.value = false
  }
}

const fetchTools = async () => {
  if (!server.value) return
  try {
    toolsLoading.value = true
    toolsError.value = null
    const result = await mcpApi.getMcpServerTools(server.value.name)
    if (result.success) {
      tools.value = result.data || []
    } else {
      toolsError.value = result.message || '获取工具列表失败'
      tools.value = []
    }
  } catch (err) {
    toolsError.value = err.message || '获取工具列表失败'
    tools.value = []
  } finally {
    toolsLoading.value = false
  }
}

const fetchConnections = async () => {
  if (!server.value) return
  try {
    connectionsLoading.value = true
    connectionsError.value = null
    const result = await mcpApi.getMcpServerConnections(server.value.name, {
      paginated: true,
      status: connectionFilter.value,
      search: connectionSearchText.value.trim(),
      page: connectionPage.value,
      page_size: connectionPageSize.value
    })
    if (result.success) {
      if (Array.isArray(result.data)) {
        connections.value = result.data
        connectionTotal.value = result.data.length
        Object.assign(connectionSummary, {
          total: result.data.length,
          active: result.data.filter(
            (connection) =>
              connection.status === 'active' &&
              isConnectionScopeMatched(connection) &&
              !isConnectionCredentialMissing(connection)
          ).length,
          attention: result.data.filter((connection) => Boolean(getConnectionIssue(connection)))
            .length,
          disabled: result.data.filter((connection) => connection.status === 'disabled').length
        })
        return
      }
      const pageData = result.data || {}
      const nextConnections = pageData.items || []
      const nextTotal = pageData.total || 0
      const nextPageSize = pageData.page_size || connectionPageSize.value
      if (connectionPage.value > 1 && nextConnections.length === 0 && nextTotal > 0) {
        connectionPage.value = Math.ceil(nextTotal / nextPageSize)
        await fetchConnections()
        return
      }
      connections.value = nextConnections
      connectionTotal.value = nextTotal
      connectionPageSize.value = nextPageSize
      Object.assign(connectionSummary, {
        total: pageData.summary?.total || 0,
        active: pageData.summary?.active || 0,
        attention: pageData.summary?.attention || 0,
        disabled: pageData.summary?.disabled || 0
      })
    } else {
      connectionsError.value = result.message || '获取连接列表失败'
      connections.value = []
      connectionTotal.value = 0
    }
  } catch (err) {
    connectionsError.value = err.message || '获取连接列表失败'
    connections.value = []
    connectionTotal.value = 0
  } finally {
    connectionsLoading.value = false
  }
}

const handleToggleTool = async (tool) => {
  if (!server.value) return
  try {
    toggleToolLoading.value = tool.name
    const result = await mcpApi.toggleMcpServerTool(server.value.name, tool.name)
    if (result.success) {
      message.success(result.message)
      const targetTool = tools.value.find((t) => t.name === tool.name)
      if (targetTool) targetTool.enabled = result.enabled
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    toggleToolLoading.value = null
  }
}

const copyToolName = async (toolName) => {
  try {
    await navigator.clipboard.writeText(toolName)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const handleTestServer = async () => {
  if (!server.value) return
  try {
    testLoading.value = server.value.name
    const result = await mcpApi.testMcpServer(server.value.name)
    if (result.success) {
      message.success(result.message)
    } else {
      message.warning(result.message || '连接失败')
    }
  } catch (err) {
    message.error(err.message || '测试失败')
  } finally {
    testLoading.value = null
  }
}

const buildConnectionCredential = () => {
  const rawCredential = parseJsonText(connectionForm.credentialText, '长期凭据', {
    allowRawString: true
  })
  if (rawCredential === undefined) return undefined
  if (rawCredential !== null) return rawCredential

  const secrets = {}
  Object.entries(connectionForm.secretValues).forEach(([key, value]) => {
    const trimmedValue = String(value || '').trim()
    if (trimmedValue) {
      setNestedSecretValue(secrets, key, trimmedValue)
    }
  })

  if (Object.keys(secrets).length === 0) {
    return null
  }
  return { secrets }
}

const validateConnectionCredential = () => {
  if (isEditingConnection.value || credentialSecretFields.value.length === 0) {
    return true
  }

  const missingFields = credentialSecretFields.value.filter(
    (fieldName) => !String(connectionForm.secretValues[fieldName] || '').trim()
  )
  if (missingFields.length === 0 || connectionForm.credentialText.trim()) {
    return true
  }

  message.error(`请填写凭据字段：${missingFields.join('、')}`)
  return false
}

const handleSubmitConnection = async () => {
  if (!server.value) return

  const scopeId = connectionForm.scopeType === 'system' ? 'global' : connectionForm.scopeId.trim()
  if (!scopeId) {
    message.error(`${scopeIdLabel.value}不能为空`)
    return
  }
  if (!validateConnectionCredential()) return

  const metaJson = parseJsonText(connectionForm.metaText, '连接元数据')
  if (metaJson === undefined) return
  const credential = buildConnectionCredential()
  if (credential === undefined) return

  try {
    connectionSubmitting.value = true
    const payload = {
      display_name: connectionForm.displayName || null,
      external_subject: connectionForm.externalSubject || null,
      meta_json: metaJson
    }
    if (credential !== null) {
      payload.credential = credential
    }

    const result = isEditingConnection.value
      ? await mcpApi.updateMcpServerConnection(
          server.value.name,
          editingConnectionId.value,
          payload
        )
      : await mcpApi.createMcpServerConnection(server.value.name, {
          scope_type: connectionForm.scopeType,
          scope_id: scopeId,
          status: 'active',
          ...payload
        })
    if (result.success) {
      message.success(isEditingConnection.value ? '连接更新成功' : '连接创建成功')
      showConnectionForm.value = false
      resetConnectionForm()
      await fetchConnections()
    } else {
      message.error(result.message || (isEditingConnection.value ? '连接更新失败' : '连接创建失败'))
    }
  } catch (err) {
    message.error(err.message || (isEditingConnection.value ? '连接更新失败' : '连接创建失败'))
  } finally {
    connectionSubmitting.value = false
  }
}

const handleToggleConnectionStatus = async (connection, checked) => {
  if (!server.value || !canToggleConnectionStatus(connection)) return
  const nextStatus = checked ? 'active' : 'disabled'
  if (connection.status === nextStatus) return
  const loadingKey = `${connection.id}:status`
  try {
    connectionActionLoading.value = loadingKey
    const result = await mcpApi.updateMcpConnectionStatus(
      server.value.name,
      connection.id,
      nextStatus
    )
    if (result.success) {
      message.success(result.message || (checked ? '连接已启用' : '连接已停用'))
      await fetchConnections()
    } else {
      message.error(result.message || '状态更新失败')
      await fetchConnections()
    }
  } catch (err) {
    message.error(err.message || '状态更新失败')
    await fetchConnections()
  } finally {
    connectionActionLoading.value = null
  }
}

const handleTestConnection = async (connection) => {
  if (!server.value || !canTestConnection(connection)) return
  const loadingKey = `${connection.id}:test`
  try {
    connectionActionLoading.value = loadingKey
    const result = await mcpApi.testMcpConnection(server.value.name, connection.id)
    if (result.success) {
      message.success(result.message || '连接测试成功')
      await fetchConnections()
    } else {
      message.error(result.message || '连接测试失败')
    }
  } catch (err) {
    message.error(err.message || '连接测试失败')
  } finally {
    connectionActionLoading.value = null
  }
}

const handleConnectionIssueAction = (connection) => {
  const issue = getConnectionIssue(connection)
  if (!issue) return
  if (issue.key === 'scope_mismatch') {
    openCreateConnectionDrawer()
    return
  }
  if (issue.key === 'missing_credentials' || issue.key === 'test_failed') {
    startEditConnection(connection)
    return
  }
  if (issue.key === 'reauth_required') {
    handleReauthorizeConnection(connection)
  }
}

const resetConnectionFilters = () => {
  connectionFilter.value = 'all'
  connectionSearchText.value = ''
}

const handleReauthorizeConnection = async (connection) => {
  if (!server.value || !canReauthorizeConnection(connection)) return
  const loadingKey = `${connection.id}:reauth`
  try {
    connectionActionLoading.value = loadingKey
    const result = await mcpApi.reauthorizeMcpConnection(server.value.name, connection.id)
    if (result.success) {
      message.success(result.message || '连接已重置')
      await fetchConnections()
    } else {
      message.error(result.message || '连接重置失败')
    }
  } catch (err) {
    message.error(err.message || '连接重置失败')
  } finally {
    connectionActionLoading.value = null
  }
}

const handleDeleteConnection = (connection) => {
  if (!server.value) return
  Modal.confirm({
    title: '确认删除连接',
    content: `确定要删除连接 "${getConnectionTitle(connection)}" 吗？`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServerConnection(server.value.name, connection.id)
        if (result.success) {
          message.success(result.message || '连接已删除')
          if (editingConnectionId.value === connection.id) {
            showConnectionForm.value = false
            resetConnectionForm()
          }
          await fetchConnections()
        } else {
          message.error(result.message || '连接删除失败')
        }
      } catch (err) {
        message.error(err.message || '连接删除失败')
      }
    }
  })
}

const handleDangerAction = async () => {
  if (!server.value) return
  if (server.value.enabled === false) {
    await handleSetServerEnabled(server.value, true)
    return
  }
  if (server.value.created_by === 'system') {
    await handleSetServerEnabled(server.value, false)
    return
  }
  confirmDeleteServer(server.value)
}

const handleSetServerEnabled = async (srv, enabled) => {
  try {
    const result = await mcpApi.updateMcpServerStatus(srv.name, enabled)
    if (result.success) {
      message.success(result.message || `MCP 已${enabled ? '添加' : '移除'}`)
      await fetchServer()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (err) {
    message.error(err.message || '操作失败')
  }
}

const confirmDeleteServer = (srv) => {
  Modal.confirm({
    title: '确认退役 MCP',
    content: `确定要退役 MCP "${srv.name}" 吗？退役后不会再被新运行加载，但配置和连接会保留。`,
    okText: '退役',
    okType: 'primary',
    cancelText: '取消',
    async onOk() {
      try {
        const result = await mcpApi.deleteMcpServer(srv.name)
        if (result.success) {
          message.success(result.message || 'MCP 已退役')
          await fetchServer()
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (err) {
        message.error(err.message || '删除失败')
      }
    }
  })
}

watch(detailTab, (tab) => {
  if (tab === 'tools' && server.value) {
    fetchTools()
  }
  if (tab === 'connections' && server.value) {
    fetchConnections()
  }
})

watch(connectionFilter, () => {
  if (detailTab.value !== 'connections') return
  if (connectionPage.value === 1) {
    fetchConnections()
  } else {
    connectionPage.value = 1
  }
})

watch(connectionSearchText, () => {
  if (connectionSearchTimer) {
    clearTimeout(connectionSearchTimer)
  }
  connectionSearchTimer = setTimeout(() => {
    if (detailTab.value !== 'connections') return
    if (connectionPage.value === 1) {
      fetchConnections()
    } else {
      connectionPage.value = 1
    }
  }, 300)
})

watch([connectionPage, connectionPageSize], () => {
  if (detailTab.value !== 'connections') return
  fetchConnections()
})

const loadScopeOptions = async () => {
  try {
    isFetchingScopeOptions.value = true
    const [usersRes, deptsRes] = await Promise.all([
      userApi.getUsers(),
      departmentApi.getDepartments()
    ])
    userList.value = usersRes || []
    departmentList.value = deptsRes || []
  } catch (err) {
    message.error('获取用户/部门列表失败: ' + err.message)
  } finally {
    isFetchingScopeOptions.value = false
  }
}

onMounted(() => {
  fetchServer()
  loadScopeOptions()
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
    font-family: @mono-font;
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

.mcp-detail {
  .connections-tab {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .connection-command-bar {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: center;
    padding: 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  .connection-command-copy {
    min-width: 0;

    h3 {
      margin: 0 0 4px;
      color: var(--gray-900);
      font-size: 16px;
      font-weight: 600;
    }

    p {
      margin: 0;
      color: var(--gray-500);
      font-size: 13px;
      line-height: 1.5;
    }
  }

  .connection-summary-strip {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
    background: var(--gray-0);
  }

  .connection-summary-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 12px 14px;

    & + .connection-summary-item {
      border-left: 1px solid var(--gray-100);
    }

    .summary-label {
      color: var(--gray-500);
      font-size: 12px;
    }

    strong {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--gray-900);
      font-size: 15px;
      font-weight: 600;
    }
  }

  .connection-list-toolbar {
    position: sticky;
    top: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  .connection-filter-group,
  .connection-page-controls {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .connection-filter-group {
    flex: 1;
  }

  .connection-page-controls {
    flex-shrink: 0;
    justify-content: flex-end;
  }

  .connection-result-count {
    color: var(--gray-500);
    font-size: 12px;
    white-space: nowrap;
  }

  .connection-search-input {
    width: min(320px, 100%);

    :deep(.ant-input-prefix) {
      color: var(--gray-500);
    }
  }

  .connection-empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 42px 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
  }

  .connection-list-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .connection-cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 12px;
  }

  .connection-card {
    padding: 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    transition:
      border-color 0.2s,
      box-shadow 0.2s;

    &:hover {
      border-color: var(--gray-300);
    }
  }

  .connection-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }

  .connection-key-info {
    min-width: 0;
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .connection-key-icon {
    color: var(--main-600);
    flex-shrink: 0;
  }

  .connection-key-copy {
    min-width: 0;

    h4 {
      margin: 0;
      overflow: hidden;
      color: var(--gray-900);
      font-size: 14px;
      font-weight: 600;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      display: block;
      margin-top: 2px;
      overflow: hidden;
      color: var(--gray-500);
      font-size: 12px;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .connection-scope-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 28px;
    flex-shrink: 0;
    gap: 5px;
    padding: 0 10px;
    border: 1px solid var(--gray-150);
    border-radius: 7px;
    background: var(--gray-25);
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;

    &.scope-system {
      border-color: var(--color-success-100);
      background: var(--color-success-50);
      color: var(--color-success-700);
    }

    &.scope-department {
      border-color: var(--color-accent-100);
      background: var(--color-accent-50);
      color: var(--color-accent-700);
    }

    &.scope-user {
      border-color: var(--color-info-100);
      background: var(--color-info-50);
      color: var(--color-info-700);
    }

    &.is-mismatch {
      border-color: var(--color-warning-100);
      background: var(--color-warning-50);
      color: var(--color-warning-900);
    }
  }

  .connection-card-content {
    margin-bottom: 10px;
  }

  .connection-issue {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
    padding: 9px 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-25);

    &.issue-warning {
      border-color: var(--color-warning-100);
      background: var(--color-warning-10);

      .issue-copy span {
        color: var(--color-warning-900);
      }
    }

    &.issue-error {
      border-color: var(--color-error-100);
      background: var(--color-error-10);

      .issue-copy span {
        color: var(--color-error-700);
      }
    }
  }

  .issue-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;

    span {
      font-size: 13px;
      font-weight: 600;
      line-height: 1.4;
    }

    small {
      overflow: hidden;
      color: var(--gray-600);
      font-size: 12px;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .issue-action {
    flex-shrink: 0;
    padding: 0;
    font-size: 12px;
    font-weight: 500;
  }

  .connection-info-item {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    margin-bottom: 6px;
    color: var(--gray-900);
    font-size: 13px;

    &:last-child {
      margin-bottom: 0;
    }

    .info-label {
      color: var(--gray-600);
      flex-shrink: 0;
    }

    .info-value {
      min-width: 0;
      color: var(--gray-900);
      word-break: break-all;
    }
  }

  .connection-card-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--gray-100);
  }

  .footer-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .switch-label {
    color: var(--gray-600);
    font-size: 12px;
  }

  .status-switch-wrap {
    display: inline-flex;
    align-items: center;
  }

  .connection-row-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 4px;
  }

  .connection-action-wrap {
    display: inline-flex;
  }

  .connection-action-btn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--gray-700);
    font-size: 12px;

    &:hover {
      color: var(--main-600);
    }
  }

  .danger-action-btn {
    color: var(--color-error-700);

    &:hover {
      background: var(--color-error-50);
      color: var(--color-error-900);
    }
  }

  .connection-pagination {
    display: flex;
    justify-content: flex-end;
    padding-top: 2px;
  }

  .detail-content-wrapper {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    background-color: var(--gray-10);
  }

  .detail-content-inner {
    max-width: 1120px;
    margin: 0 auto;
    padding: 16px var(--page-padding);
  }
}

.connection-drawer-form {
  display: flex;
  min-height: 100%;
  flex-direction: column;
  padding: 18px 20px 0;

  :deep(.ant-form-item) {
    margin-bottom: 0;
  }

  :deep(.ant-form-item-label > label) {
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
  }
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-bottom: 18px;

  & + .drawer-section {
    padding-top: 18px;
    border-top: 1px solid var(--gray-100);
  }
}

.drawer-section-title {
  display: flex;
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

.scope-option-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(136px, 1fr));
  gap: 8px;
}

.scope-option {
  display: flex;
  min-height: 78px;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-700);
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.15s ease,
    background-color 0.15s ease,
    color 0.15s ease;

  span {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
  }

  small {
    color: var(--gray-500);
    font-size: 12px;
  }

  &:hover:not(:disabled) {
    border-color: var(--main-300);
    background: var(--main-10);
    color: var(--main-color);
  }

  &.active {
    border-color: var(--main-color);
    background: var(--main-30);
    color: var(--main-color);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.7;
  }
}

.secret-field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.connection-advanced-collapse {
  margin: 0 -4px 12px;

  :deep(.ant-collapse-header) {
    padding: 10px 4px;
    color: var(--gray-600);
    font-size: 13px;
  }

  :deep(.ant-collapse-content-box) {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 4px 4px 12px;
  }
}

.connection-drawer-footer {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: auto -20px 0;
  padding: 14px 20px;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-0);
}

@media (max-width: 980px) {
  .mcp-detail {
    .connection-list-toolbar {
      align-items: flex-start;
      flex-direction: column;
    }

    .connection-filter-group,
    .connection-page-controls {
      width: 100%;
      justify-content: space-between;
    }

    .connection-filter-group {
      align-items: flex-start;
      flex-direction: column;
    }

    .connection-search-input {
      width: 100%;
    }

    .connection-summary-strip {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .connection-summary-item:nth-child(3) {
      border-left: 0;
      border-top: 1px solid var(--gray-100);
    }

    .connection-summary-item:nth-child(4) {
      border-top: 1px solid var(--gray-100);
    }

    .connection-row-actions {
      justify-content: flex-start;
    }

    .connection-card-footer {
      align-items: flex-start;
      flex-direction: column;
    }
  }
}

@media (max-width: 640px) {
  .mcp-detail {
    .connection-command-bar {
      align-items: flex-start;
      flex-direction: column;
    }

    .connection-issue {
      align-items: flex-start;
      flex-direction: column;
    }

    .connection-pagination {
      justify-content: flex-start;
    }

    .connection-summary-strip,
    .connection-cards-grid,
    .scope-option-grid,
    .secret-field-grid {
      grid-template-columns: 1fr;
    }
  }
}
</style>
