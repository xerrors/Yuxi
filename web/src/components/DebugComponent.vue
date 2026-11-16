<template>
  <a-modal
    v-model:open="showModal"
    :title="null"
    :closable="false"
    :footer="null"
    :maskClosable="true"
    :destroyOnClose="true"
    width="94%"
    style="max-width: 1400px; top: 2.5vh; padding-bottom: 0"
    :bodyStyle="{ padding: 0, height: '95vh', overflow: 'hidden' }"
    class="debug-modal"
    wrap-class-name="debug-modal-wrap"
  >
    <div :class="['debug-panel-wrapper', { fullscreen: state.isFullscreen }]" ref="debugWrapper">
      <!-- 顶部单行 Header 栏 -->
      <div class="panel-header">
        <div class="header-title-group">
          <span class="main-title">系统调试与诊断</span>
          <span class="env-badge">SuperAdmin</span>
        </div>

        <div class="header-quick-actions">
          <!-- 对话 Debug 开关 -->
          <div
            class="debug-mode-switch-badge"
            :class="{ active: infoStore.debugMode }"
            @click="toggleDebugMode"
            title="点击切换消息调试模式"
          >
            <Bug :size="13" class="switch-icon" />
            <span class="switch-label"
              >对话 Debug: {{ infoStore.debugMode ? '已开启' : '已关闭' }}</span
            >
          </div>

          <!-- 全屏切换 -->
          <a-button
            size="small"
            class="header-action-btn"
            @click="toggleFullscreen"
            :title="state.isFullscreen ? '退出全屏' : '全屏显示'"
          >
            <template #icon>
              <Minimize2 v-if="state.isFullscreen" :size="13" />
              <Maximize2 v-else :size="13" />
            </template>
          </a-button>

          <!-- 关闭按钮 -->
          <a-button
            size="small"
            class="header-action-btn close-btn"
            @click="showModal = false"
            title="关闭调试面板"
          >
            <template #icon><X :size="14" /></template>
          </a-button>
        </div>
      </div>

      <!-- 主体内容区：左侧紧凑导航 + 右侧面板 -->
      <div class="panel-main-layout">
        <!-- 侧边导航栏 (更窄、深色 icon、灰色系) -->
        <div class="panel-sidebar-nav">
          <div
            v-for="tab in tabs"
            :key="tab.key"
            class="nav-tab-item"
            :class="{ active: state.activeTab === tab.key }"
            @click="switchTab(tab.key)"
          >
            <component :is="tab.icon" :size="15" class="tab-icon" />
            <span class="tab-label">{{ tab.label }}</span>
            <span v-if="tab.badge" class="tab-badge">{{ tab.badge }}</span>
          </div>
        </div>

        <!-- 右侧内容视口 -->
        <div class="panel-content-viewport">
          <!-- ==================== TAB 1: 系统日志 ==================== -->
          <div v-show="state.activeTab === 'logs'" class="tab-pane logs-pane">
            <div class="pane-toolbar">
              <div class="toolbar-left">
                <!-- 日志级别多选标签 -->
                <div class="level-filter-chips">
                  <div
                    v-for="level in logLevels"
                    :key="level.value"
                    class="level-chip"
                    :class="[
                      `level-${level.value.toLowerCase()}`,
                      { selected: isLogLevelSelected(level.value) }
                    ]"
                    @click="toggleLogLevel(level.value)"
                  >
                    <span class="level-indicator"></span>
                    <span class="level-text">{{ level.label }}</span>
                  </div>
                </div>

                <!-- 搜索框 -->
                <a-input
                  v-model:value="state.searchText"
                  placeholder="过滤日志关键字..."
                  allow-clear
                  size="small"
                  class="log-search-input"
                >
                  <template #prefix>
                    <Search :size="13" class="search-icon" />
                  </template>
                </a-input>

                <span class="log-count-meta" v-if="processedLogs.length">
                  匹配 <strong>{{ processedLogs.length }}</strong> / {{ state.rawLogs.length }} 行
                </span>
              </div>

              <div class="toolbar-right">
                <a-tooltip title="刷新日志">
                  <a-button
                    size="small"
                    :loading="state.fetching"
                    @click="fetchLogs"
                    class="tool-btn icon-only"
                  >
                    <template #icon><RefreshCw :size="13" /></template>
                  </a-button>
                </a-tooltip>

                <a-tooltip :title="state.autoRefresh ? '停止 5s 自动刷新' : '开启 5s 自动刷新'">
                  <a-button
                    size="small"
                    :type="state.autoRefresh ? 'primary' : 'default'"
                    @click="toggleAutoRefresh(!state.autoRefresh)"
                    class="tool-btn auto-refresh-btn"
                    :class="{ 'auto-refreshing': state.autoRefresh }"
                  >
                    <template #icon><Clock :size="13" /></template>
                    <span class="refresh-countdown">{{
                      state.autoRefresh ? '自动刷新 (5s)' : '自动刷新: 关'
                    }}</span>
                  </a-button>
                </a-tooltip>

                <a-tooltip title="复制当前过滤的全部日志">
                  <a-button size="small" @click="copyAllLogs" class="tool-btn icon-only">
                    <template #icon>
                      <Check v-if="state.isLogsCopied" :size="13" class="copied-icon" />
                      <Copy v-else :size="13" />
                    </template>
                  </a-button>
                </a-tooltip>

                <a-tooltip title="下载日志为 .log 文件">
                  <a-button size="small" @click="downloadLogs" class="tool-btn icon-only">
                    <template #icon><Download :size="13" /></template>
                  </a-button>
                </a-tooltip>

                <a-tooltip :title="state.autoScroll ? '当前跟随最新日志' : '滚动到底部并恢复跟随'">
                  <a-button
                    size="small"
                    :type="state.autoScroll ? 'primary' : 'default'"
                    @click="resumeLogAutoScroll"
                    class="tool-btn icon-only"
                  >
                    <template #icon><ArrowDownToLine :size="13" /></template>
                  </a-button>
                </a-tooltip>

                <a-tooltip title="自动换行">
                  <a-button
                    size="small"
                    :type="state.wrapLines ? 'primary' : 'default'"
                    @click="state.wrapLines = !state.wrapLines"
                    class="tool-btn icon-only"
                  >
                    <template #icon><WrapText :size="13" /></template>
                  </a-button>
                </a-tooltip>

                <a-tooltip title="清空当前日志显示">
                  <a-button size="small" @click="clearLogs" class="tool-btn icon-only danger">
                    <template #icon><Trash2 :size="13" /></template>
                  </a-button>
                </a-tooltip>
              </div>
            </div>

            <!-- Terminal 日志展示区 -->
            <div
              ref="logContainer"
              class="terminal-log-container"
              :class="{ 'wrap-lines': state.wrapLines }"
              @scroll="handleLogScroll"
            >
              <div v-if="processedLogs.length" class="terminal-lines">
                <div
                  v-for="(log, index) in processedLogs"
                  :key="index"
                  :class="['terminal-line', `level-${log.level.toLowerCase()}`]"
                >
                  <span class="line-no">{{ index + 1 }}</span>
                  <span class="line-time">{{ formatTimestamp(log.timestamp) }}</span>
                  <span class="line-level">{{ log.level }}</span>
                  <span class="line-module" :title="log.module">{{ log.module }}</span>
                  <span class="line-msg">{{ log.message }}</span>
                  <button
                    type="button"
                    class="line-copy-btn"
                    title="复制此行"
                    @click.stop="copyLine(log.raw)"
                  >
                    <Copy :size="11" />
                  </button>
                </div>
              </div>
              <div v-else-if="state.fetching" class="terminal-empty">
                <a-spin size="small" />
                <span>正在获取最新日志...</span>
              </div>
              <div v-else class="terminal-empty">
                <span>暂无符合条件的日志</span>
              </div>
            </div>

            <div v-if="error" class="log-error-banner">
              {{ error }}
            </div>
          </div>

          <!-- ==================== TAB 2: 系统配置 ==================== -->
          <div v-show="state.activeTab === 'config'" class="tab-pane config-pane">
            <div class="pane-toolbar">
              <div class="toolbar-left">
                <a-input
                  v-model:value="state.configSearch"
                  placeholder="搜索配置键名或值..."
                  allow-clear
                  size="small"
                  class="config-search-input"
                >
                  <template #prefix><Search :size="13" /></template>
                </a-input>

                <div class="view-mode-toggle">
                  <button
                    type="button"
                    class="mode-btn"
                    :class="{ active: state.configViewMode === 'table' }"
                    @click="state.configViewMode = 'table'"
                  >
                    表格视图
                  </button>
                  <button
                    type="button"
                    class="mode-btn"
                    :class="{ active: state.configViewMode === 'json' }"
                    @click="state.configViewMode = 'json'"
                  >
                    JSON 视图
                  </button>
                </div>
              </div>

              <div class="toolbar-right">
                <a-button size="small" @click="refreshConfigData" :loading="state.loadingConfig">
                  <template #icon><RefreshCw :size="13" /></template>
                  刷新配置
                </a-button>
                <a-button size="small" @click="copyConfigJson">
                  <template #icon>
                    <Check v-if="state.isConfigCopied" :size="13" class="copied-icon" />
                    <Copy v-else :size="13" />
                  </template>
                  复制配置
                </a-button>
              </div>
            </div>

            <!-- 表格视图 -->
            <div v-if="state.configViewMode === 'table'" class="config-table-container">
              <table class="diagnostic-table">
                <thead>
                  <tr>
                    <th style="width: 220px">配置项 (Key)</th>
                    <th style="width: 100px">类型</th>
                    <th>当前值 (Value)</th>
                    <th style="width: 200px">说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in filteredConfigItems" :key="item.key">
                    <td class="code-cell font-mono">{{ item.key }}</td>
                    <td>
                      <span class="type-pill">{{ item.type }}</span>
                    </td>
                    <td class="value-cell font-mono">
                      {{ formatConfigValue(item.value) }}
                    </td>
                    <td class="desc-cell">{{ item.description || '-' }}</td>
                  </tr>
                  <tr v-if="filteredConfigItems.length === 0">
                    <td colspan="4" class="empty-cell">未找到匹配的配置项</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- JSON 视图 -->
            <div v-else class="config-json-container">
              <pre class="json-code-box"><code>{{ formattedConfigJson }}</code></pre>
            </div>
          </div>

          <!-- ==================== TAB 3: 用户与会话 ==================== -->
          <div v-show="state.activeTab === 'user'" class="tab-pane user-pane">
            <div class="user-cards-grid">
              <!-- 当前登录用户卡片 -->
              <div class="diagnostic-card">
                <div class="card-header">
                  <User :size="15" class="card-icon" />
                  <span class="card-title">当前登录用户信息</span>
                </div>
                <div class="card-body">
                  <div class="user-profile-header">
                    <FallbackAvatar
                      :src="userStore.avatar"
                      :name="userStore.username"
                      :seed="userStore.uid || userStore.username"
                      kind="user"
                      :size="40"
                      shape="circle"
                    />
                    <div class="user-title-box">
                      <div class="username-row">
                        <span class="username">{{ userStore.username }}</span>
                        <span class="role-tag font-mono">{{ userStore.userRole }}</span>
                      </div>
                      <div class="uid-row font-mono">UID: {{ userStore.uid || '无' }}</div>
                    </div>
                  </div>

                  <div class="info-kv-list">
                    <div class="kv-row">
                      <span class="k">User ID</span>
                      <span class="v font-mono">{{ userStore.userId }}</span>
                    </div>
                    <div class="kv-row">
                      <span class="k">手机号</span>
                      <span class="v">{{ userStore.phoneNumber || '未绑定' }}</span>
                    </div>
                    <div class="kv-row">
                      <span class="k">管理员权限 (isAdmin)</span>
                      <span class="v font-mono">{{ userStore.isAdmin ? 'true' : 'false' }}</span>
                    </div>
                    <div class="kv-row">
                      <span class="k">超级管理员 (isSuperAdmin)</span>
                      <span class="v font-mono">{{
                        userStore.isSuperAdmin ? 'true' : 'false'
                      }}</span>
                    </div>
                    <div class="kv-row">
                      <span class="k">Token 状态</span>
                      <span class="v font-mono">
                        {{
                          userStore.token
                            ? `${userStore.token.slice(0, 12)}...${userStore.token.slice(-6)}`
                            : '未登录'
                        }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 切换用户模拟 (Super Admin) -->
              <div class="diagnostic-card">
                <div class="card-header">
                  <Users :size="15" class="card-icon" />
                  <span class="card-title">用户模拟切换 (Impersonate)</span>
                  <span class="badge-subtle">免密切换</span>
                </div>
                <div class="card-body">
                  <div class="user-switcher-toolbar">
                    <a-input
                      v-model:value="state.userSearch"
                      placeholder="搜索用户名或UID..."
                      allow-clear
                      size="small"
                      class="user-search-input"
                    >
                      <template #prefix><Search :size="13" /></template>
                    </a-input>
                    <a-button size="small" @click="fetchUsers" :loading="state.loadingUsers">
                      <template #icon><RefreshCw :size="13" /></template>
                      刷新列表
                    </a-button>
                  </div>

                  <div class="user-list-scroll">
                    <div
                      v-for="user in filteredUsers"
                      :key="user.id"
                      class="user-item-row"
                      :class="{ current: user.id === userStore.userId }"
                    >
                      <div class="user-meta-left">
                        <FallbackAvatar
                          :src="user.avatar"
                          :name="user.username"
                          :seed="user.uid || user.username"
                          kind="user"
                          :size="26"
                          shape="circle"
                        />
                        <div class="user-details">
                          <span class="name">{{ user.username }}</span>
                          <span class="sub font-mono">ID: {{ user.id }} · {{ user.role }}</span>
                        </div>
                      </div>

                      <div class="user-meta-right">
                        <span v-if="user.id === userStore.userId" class="current-badge">
                          当前
                        </span>
                        <a-button
                          v-else
                          size="small"
                          @click="switchToUser(user)"
                          :loading="state.switchingUserId === user.id"
                        >
                          切换
                        </a-button>
                      </div>
                    </div>
                    <div v-if="filteredUsers.length === 0" class="empty-hint">
                      {{ state.loadingUsers ? '正在加载用户列表...' : '未找到匹配用户' }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- ==================== TAB 4: 本地存储与调试 ==================== -->
          <div v-show="state.activeTab === 'storage'" class="tab-pane storage-pane">
            <!-- 顶部工具与统计栏 -->
            <div class="pane-toolbar">
              <div class="toolbar-left">
                <a-input
                  v-model:value="state.storageSearch"
                  placeholder="搜索 LocalStorage 键名或内容..."
                  allow-clear
                  size="small"
                  class="storage-search-input"
                >
                  <template #prefix><Search :size="13" /></template>
                </a-input>

                <span class="storage-stats-pill font-mono">
                  共 {{ state.localStorageList.length }} 项 · 预估 {{ totalStorageSizeText }}
                </span>
              </div>

              <div class="toolbar-right">
                <a-button size="small" @click="openAddStorageModal">
                  <template #icon><Plus :size="13" /></template>
                  新增键值
                </a-button>

                <a-button size="small" @click="loadLocalStorageItems">
                  <template #icon><RefreshCw :size="13" /></template>
                  刷新存储
                </a-button>

                <a-button size="small" @click="exportStorageJson">
                  <template #icon>
                    <Check v-if="state.isStorageCopied" :size="13" class="copied-icon" />
                    <Copy v-else :size="13" />
                  </template>
                  复制全部 JSON
                </a-button>

                <a-button size="small" danger @click="confirmClearAllStorage">
                  <template #icon><Trash2 :size="13" /></template>
                  清空全部
                </a-button>
              </div>
            </div>

            <!-- LocalStorage 完整表格 -->
            <div class="storage-table-container">
              <table class="diagnostic-table">
                <thead>
                  <tr>
                    <th style="width: 240px">键名 (Key)</th>
                    <th style="width: 90px">大小</th>
                    <th>值预览 (Value Preview)</th>
                    <th style="width: 170px; text-align: right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in filteredStorageList" :key="item.key">
                    <td class="code-cell font-mono">
                      <div class="storage-key-row">
                        <span class="key-name" :title="item.key">{{ item.key }}</span>
                        <span v-if="item.isSystem" class="badge-system">系统</span>
                      </div>
                    </td>
                    <td class="font-mono text-muted">{{ item.sizeText }}</td>
                    <td class="value-cell font-mono" :title="item.value">
                      <span class="storage-preview-text">{{ item.preview }}</span>
                    </td>
                    <td style="text-align: right">
                      <div class="table-actions-group">
                        <a-button
                          size="small"
                          type="text"
                          class="table-action-btn"
                          @click="viewStorageDetail(item)"
                          title="查看完整数据"
                        >
                          <template #icon><Eye :size="13" /></template>
                        </a-button>
                        <a-button
                          size="small"
                          type="text"
                          class="table-action-btn"
                          @click="copyStorageValue(item.value)"
                          title="复制内容"
                        >
                          <template #icon><Copy :size="13" /></template>
                        </a-button>
                        <a-button
                          size="small"
                          type="text"
                          class="table-action-btn"
                          @click="editStorageItem(item)"
                          title="编辑值"
                        >
                          <template #icon><Edit3 :size="13" /></template>
                        </a-button>
                        <a-button
                          size="small"
                          type="text"
                          danger
                          class="table-action-btn danger"
                          @click="deleteStorageKey(item.key)"
                          title="删除此键"
                        >
                          <template #icon><Trash2 :size="13" /></template>
                        </a-button>
                      </div>
                    </td>
                  </tr>
                  <tr v-if="filteredStorageList.length === 0">
                    <td colspan="4" class="empty-cell">未找到匹配的 LocalStorage 项</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 底部：调试开关与全 Store 重载 -->
            <div class="storage-bottom-cards mt-12">
              <div class="diagnostic-card">
                <div class="card-header">
                  <Bug :size="15" class="card-icon" />
                  <span class="card-title">调试选项与 Store 控制</span>
                </div>
                <div class="card-body debug-settings-body">
                  <div class="setting-inline-row">
                    <div class="setting-text">
                      <span class="setting-name">对话消息 Debug 模式</span>
                      <span class="setting-desc"
                        >在 Agent 侧栏查看消息时序、工具名称与原始 JSON</span
                      >
                    </div>
                    <a-switch :checked="infoStore.debugMode" @change="toggleDebugMode" />
                  </div>

                  <div class="setting-inline-row">
                    <div class="setting-text">
                      <span class="setting-name">全屏调试快捷键</span>
                      <span class="setting-desc"
                        >按 <kbd>Ctrl + Shift + D</kbd> (Mac 下 <kbd>Cmd + Shift + D</kbd>)
                        快速开启/关闭</span
                      >
                    </div>
                    <a-button size="small" @click="reloadAllStores">
                      <template #icon><RefreshCw :size="13" /></template>
                      重载全部 Store
                    </a-button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 查看/编辑 LocalStorage 弹窗 -->
    <a-modal
      v-model:open="state.storageModalVisible"
      :title="
        state.storageModalMode === 'edit'
          ? '编辑 LocalStorage'
          : state.storageModalMode === 'add'
            ? '新增 LocalStorage 键值'
            : '查看 LocalStorage 详情'
      "
      :footer="null"
      width="640px"
    >
      <div class="storage-modal-form">
        <div class="form-item">
          <label class="form-label">键名 (Key)</label>
          <a-input
            v-model:value="state.modalStorageKey"
            :disabled="state.storageModalMode === 'view' || state.storageModalMode === 'edit'"
            placeholder="例如: theme / yuxi_custom_config"
          />
        </div>

        <div class="form-item">
          <div class="label-with-action">
            <label class="form-label">值 (Value)</label>
            <div class="action-links">
              <a-button
                v-if="state.storageModalMode !== 'view'"
                size="small"
                type="link"
                @click="formatModalValueJson"
              >
                格式化 JSON
              </a-button>
              <a-button size="small" type="link" @click="copyStorageValue(state.modalStorageValue)">
                复制
              </a-button>
            </div>
          </div>
          <a-textarea
            v-model:value="state.modalStorageValue"
            :rows="12"
            :disabled="state.storageModalMode === 'view'"
            class="storage-value-textarea font-mono"
            placeholder="输入字符串或 JSON 内容..."
          />
        </div>

        <div class="modal-footer-actions">
          <a-button @click="state.storageModalVisible = false">关闭</a-button>
          <a-button
            v-if="state.storageModalMode !== 'view'"
            type="primary"
            @click="saveStorageModalValue"
          >
            保存
          </a-button>
        </div>
      </div>
    </a-modal>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onActivated, onUnmounted, nextTick, watch } from 'vue'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import { useDatabaseStore } from '@/stores/database'
import { useAgentStore } from '@/stores/agent'
import { useInfoStore } from '@/stores/info'
import { useThrottleFn } from '@vueuse/core'
import { message, Modal } from 'ant-design-vue'
import {
  Bug,
  Search,
  RefreshCw,
  Clock,
  Copy,
  Check,
  Download,
  Trash2,
  Maximize2,
  Minimize2,
  X,
  FileText,
  Settings,
  User,
  Users,
  HardDrive,
  ArrowDownToLine,
  WrapText,
  Plus,
  Eye,
  Edit3
} from '@lucide/vue'
import dayjs from '@/utils/time'
import { authApi } from '@/apis/auth_api'
import { configApi } from '@/apis/system_api'
import { checkSuperAdminPermission } from '@/stores/user'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { copyTextToClipboard } from '@/utils/clipboard'
import { isLogContainerAtBottom } from '@/utils/logScroll'

const showModal = defineModel('show')

const configStore = useConfigStore()
const userStore = useUserStore()
const databaseStore = useDatabaseStore()
const agentStore = useAgentStore()
const infoStore = useInfoStore()

// 日志级别定义
const logLevels = [
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
  { value: 'DEBUG', label: 'DEBUG' }
]

const debugWrapper = ref(null)
const logContainer = ref(null)
let autoRefreshInterval = null

// 状态管理
const state = reactive({
  activeTab: 'logs',
  fetching: false,
  autoRefresh: true, // 默认开启自动刷新
  autoScroll: true,
  wrapLines: false,
  searchText: '',
  selectedLevels: ['INFO', 'WARNING', 'ERROR', 'DEBUG'],
  rawLogs: [],
  isFullscreen: false,
  isLogsCopied: false,
  isConfigCopied: false,
  configSearch: '',
  configViewMode: 'table',
  loadingConfig: false,
  userSearch: '',
  loadingUsers: false,
  users: [],
  switchingUserId: null,
  // LocalStorage 状态
  localStorageList: [],
  storageSearch: '',
  isStorageCopied: false,
  storageModalVisible: false,
  storageModalMode: 'view', // 'view' | 'edit' | 'add'
  modalStorageKey: '',
  modalStorageValue: ''
})

const error = ref('')

// 解析日志行
function parseLogLine(line) {
  const match = line.match(
    /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)\s*-\s*(\w+)\s*-\s*([^-]+?)\s*-\s*(.+)$/
  )
  if (match) {
    return {
      timestamp: match[1],
      level: match[2],
      module: match[3].trim(),
      message: match[4].trim(),
      raw: line
    }
  }
  return {
    timestamp: '',
    level: 'INFO',
    module: '',
    message: line,
    raw: line
  }
}

// 处理日志过滤
const processedLogs = computed(() => {
  return state.rawLogs.map(parseLogLine).filter((log) => {
    if (!log) return false
    if (log.level && !state.selectedLevels.includes(log.level.toUpperCase())) {
      return false
    }
    if (!state.searchText) return true
    return log.raw.toLowerCase().includes(state.searchText.toLowerCase())
  })
})

// 导航 Tab 定义 (移除了知识库状态与智能体运行时)
const tabs = computed(() => [
  { key: 'logs', label: '系统日志', icon: FileText, badge: processedLogs.value.length || null },
  { key: 'config', label: '系统配置', icon: Settings },
  { key: 'user', label: '用户与会话', icon: User },
  {
    key: 'storage',
    label: '本地存储',
    icon: HardDrive,
    badge: state.localStorageList.length || null
  }
])

// 格式化时间戳
function formatTimestamp(timestamp) {
  if (!timestamp) return ''
  try {
    let normalized = timestamp.replace(',', '.')
    if (!/\.\d{3}$/.test(normalized)) {
      normalized += '.000'
    }
    const date = dayjs(normalized)
    return date.isValid() ? date.format('HH:mm:ss.SSS') : timestamp
  } catch {
    return timestamp
  }
}

// 获取系统日志
async function fetchLogs() {
  if (!checkSuperAdminPermission()) return

  state.fetching = true
  try {
    error.value = ''
    const levelsParam = state.selectedLevels.join(',')
    const logData = await configApi.getLogs(levelsParam)
    state.rawLogs = logData.log.split('\n').filter((line) => line.trim())

    await nextTick()
    if (state.autoScroll) {
      scrollToBottom()
    }
  } catch (err) {
    error.value = `获取日志失败: ${err.message}`
  } finally {
    state.fetching = false
  }
}

const scrollToBottom = useThrottleFn(() => {
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, 100)

function handleLogScroll() {
  state.autoScroll = isLogContainerAtBottom(logContainer.value)
}

function resumeLogAutoScroll() {
  state.autoScroll = true
  scrollToBottom()
}

// 清空日志
function clearLogs() {
  state.rawLogs = []
}

// 复制全部日志
async function copyAllLogs() {
  const text = processedLogs.value.map((l) => l.raw).join('\n')
  if (!text) return
  try {
    await copyTextToClipboard(text)
    state.isLogsCopied = true
    message.success('已复制过滤后的全部日志')
    setTimeout(() => {
      state.isLogsCopied = false
    }, 2000)
  } catch {
    message.error('复制失败')
  }
}

// 复制单行日志
async function copyLine(rawLine) {
  try {
    await copyTextToClipboard(rawLine)
    message.success('已复制该行日志')
  } catch {
    message.error('复制失败')
  }
}

// 下载日志
function downloadLogs() {
  const text = processedLogs.value.map((l) => l.raw).join('\n')
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `yuxi-api-log-${dayjs().format('YYYYMMDD_HHmmss')}.log`
  a.click()
  URL.revokeObjectURL(url)
}

// 日志级别过滤
function isLogLevelSelected(level) {
  return state.selectedLevels.includes(level)
}

function toggleLogLevel(level) {
  const currentLevels = [...state.selectedLevels]
  const index = currentLevels.indexOf(level)

  if (index > -1) {
    if (currentLevels.length === 1) return
    currentLevels.splice(index, 1)
  } else {
    currentLevels.push(level)
  }

  state.selectedLevels = currentLevels
  fetchLogs()
}

// 自动刷新控制
function startAutoRefresh() {
  if (autoRefreshInterval) clearInterval(autoRefreshInterval)
  autoRefreshInterval = setInterval(fetchLogs, 5000)
  state.autoRefresh = true
}

function stopAutoRefresh() {
  if (autoRefreshInterval) {
    clearInterval(autoRefreshInterval)
    autoRefreshInterval = null
  }
}

function toggleAutoRefresh(value) {
  if (!checkSuperAdminPermission()) return
  state.autoRefresh = value
  if (value) {
    startAutoRefresh()
  } else {
    stopAutoRefresh()
  }
}

// 切换 Tab
function switchTab(tabKey) {
  state.activeTab = tabKey
  if (tabKey === 'logs' && state.rawLogs.length === 0) {
    fetchLogs()
  } else if (tabKey === 'config' && Object.keys(configStore.config || {}).length === 0) {
    refreshConfigData()
  } else if (tabKey === 'user' && state.users.length === 0) {
    fetchUsers()
  } else if (tabKey === 'storage') {
    loadLocalStorageItems()
  }
}

// 监听弹窗可见性，关闭后不保留日志轮询。
watch(showModal, (isOpen) => {
  if (!isOpen) {
    stopAutoRefresh()
    return
  }

  loadLocalStorageItems()
  if (state.activeTab === 'logs') {
    fetchLogs()
  }
  if (state.autoRefresh) {
    startAutoRefresh()
  }
  if (userStore.isSuperAdmin && state.users.length === 0) {
    fetchUsers()
  }
})

// 全屏切换
const toggleFullscreen = async () => {
  if (!checkSuperAdminPermission()) return

  try {
    if (!state.isFullscreen) {
      if (debugWrapper.value.requestFullscreen) {
        await debugWrapper.value.requestFullscreen()
      } else if (debugWrapper.value.webkitRequestFullscreen) {
        await debugWrapper.value.webkitRequestFullscreen()
      }
    } else {
      if (document.exitFullscreen) {
        await document.exitFullscreen()
      } else if (document.webkitExitFullscreen) {
        await document.webkitExitFullscreen()
      }
    }
  } catch (err) {
    console.error('全屏切换失败:', err)
  }
}

const handleFullscreenChange = () => {
  state.isFullscreen = Boolean(document.fullscreenElement || document.webkitFullscreenElement)
}

onMounted(() => {
  document.addEventListener('fullscreenchange', handleFullscreenChange)
  document.addEventListener('webkitfullscreenchange', handleFullscreenChange)
})

onActivated(() => {
  if (!showModal.value) return
  if (state.autoRefresh) {
    startAutoRefresh()
  } else {
    fetchLogs()
  }
})

onUnmounted(() => {
  stopAutoRefresh()
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
  document.removeEventListener('webkitfullscreenchange', handleFullscreenChange)
})

// Debug 模式开关
const toggleDebugMode = () => {
  if (!checkSuperAdminPermission()) return
  infoStore.toggleDebugMode()
  if (infoStore.debugMode) {
    message.success('已开启对话 Debug 模式：消息将展示调试元数据')
  } else {
    message.info('已关闭对话 Debug 模式')
  }
  loadLocalStorageItems()
}

// ==================== CONFIG TAB LOGIC ====================
async function refreshConfigData() {
  state.loadingConfig = true
  try {
    await configStore.refreshConfig()
    message.success('系统配置已刷新')
  } catch (err) {
    message.error(`刷新配置失败: ${err.message}`)
  } finally {
    state.loadingConfig = false
  }
}

const filteredConfigItems = computed(() => {
  const cfg = configStore.config || {}
  const items = []
  for (const [key, val] of Object.entries(cfg)) {
    if (key === '_config_items') continue
    const meta = cfg._config_items?.[key] || {}
    items.push({
      key,
      value: val,
      type: meta.type || typeof val,
      description: meta.des || ''
    })
  }

  if (!state.configSearch) return items
  const q = state.configSearch.toLowerCase()
  return items.filter(
    (i) =>
      i.key.toLowerCase().includes(q) ||
      String(i.value).toLowerCase().includes(q) ||
      i.description.toLowerCase().includes(q)
  )
})

const formattedConfigJson = computed(() => {
  return JSON.stringify(configStore.config || {}, null, 2)
})

const formatConfigValue = (val) => {
  if (val === null || val === undefined) return 'null'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

const copyConfigJson = async () => {
  try {
    await copyTextToClipboard(formattedConfigJson.value)
    state.isConfigCopied = true
    message.success('已复制系统配置 JSON')
    setTimeout(() => {
      state.isConfigCopied = false
    }, 2000)
  } catch {
    message.error('复制失败')
  }
}

// ==================== USER TAB LOGIC ====================
async function fetchUsers() {
  state.loadingUsers = true
  try {
    state.users = await userStore.getUsers()
  } catch (err) {
    message.error(`获取用户列表失败: ${err.message}`)
  } finally {
    state.loadingUsers = false
  }
}

const filteredUsers = computed(() => {
  if (!state.userSearch) return state.users
  const q = state.userSearch.toLowerCase()
  return state.users.filter(
    (u) =>
      u.username?.toLowerCase().includes(q) ||
      u.uid?.toLowerCase().includes(q) ||
      String(u.id).includes(q)
  )
})

const switchToUser = async (user) => {
  if (!checkSuperAdminPermission()) return

  Modal.confirm({
    title: '危险操作确认',
    content: `确定要切换为用户 "${user.username}" 吗？切换后将获得该用户的全部操作权限。`,
    okText: '确认切换',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      state.switchingUserId = user.id
      try {
        const data = await authApi.impersonateUser(user.id)
        localStorage.setItem('user_token', data.access_token)
        message.success(`已切换为用户: ${user.username}`)
        showModal.value = false
        window.location.reload()
      } catch (err) {
        message.error(`切换失败: ${err.message}`)
      } finally {
        state.switchingUserId = null
      }
    }
  })
}

// ==================== LOCALSTORAGE TAB LOGIC ====================
const systemKeyPrefixes = ['user_token', 'yuxi_', 'theme', 'vueuse', 'loglevel']

const formatBytes = (bytes) => {
  if (bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function loadLocalStorageItems() {
  try {
    const list = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key === null) continue
      const value = localStorage.getItem(key) || ''
      const byteSize = new Blob([value]).size
      const isSystem = systemKeyPrefixes.some((p) => key.startsWith(p))
      let preview = value
      if (preview.length > 120) {
        preview = `${preview.slice(0, 120)}...`
      }
      list.push({
        key,
        value,
        bytes: byteSize,
        sizeText: formatBytes(byteSize),
        preview,
        isSystem
      })
    }
    // 排序：系统 key 优先，再按 key 名称字母排序
    list.sort((a, b) => {
      if (a.isSystem && !b.isSystem) return -1
      if (!a.isSystem && b.isSystem) return 1
      return a.key.localeCompare(b.key)
    })
    state.localStorageList = list
  } catch (err) {
    console.error('读取 LocalStorage 失败:', err)
  }
}

const totalStorageSizeText = computed(() => {
  const totalBytes = state.localStorageList.reduce((acc, cur) => acc + (cur.bytes || 0), 0)
  return formatBytes(totalBytes)
})

const filteredStorageList = computed(() => {
  if (!state.storageSearch) return state.localStorageList
  const q = state.storageSearch.toLowerCase()
  return state.localStorageList.filter(
    (item) => item.key.toLowerCase().includes(q) || item.value.toLowerCase().includes(q)
  )
})

const copyStorageValue = async (val) => {
  try {
    await copyTextToClipboard(val)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const exportStorageJson = async () => {
  try {
    const data = {}
    for (const item of state.localStorageList) {
      data[item.key] = item.value
    }
    await copyTextToClipboard(JSON.stringify(data, null, 2))
    state.isStorageCopied = true
    message.success('已复制全部 LocalStorage JSON')
    setTimeout(() => {
      state.isStorageCopied = false
    }, 2000)
  } catch {
    message.error('复制失败')
  }
}

const deleteStorageKey = (key) => {
  Modal.confirm({
    title: '确认删除存储项',
    content: `确定要删除 LocalStorage 键 "${key}" 吗？如果为系统关键键可能影响登录或配置状态。`,
    okText: '确认删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => {
      localStorage.removeItem(key)
      loadLocalStorageItems()
      message.success(`已删除键: ${key}`)
    }
  })
}

const confirmClearAllStorage = () => {
  Modal.confirm({
    title: '危险操作：清空全部 LocalStorage',
    content: '清空全部本地存储将清除 Token、用户凭证与缓存，您将被登出并需要重新登录。确定继续吗？',
    okText: '确认清空',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => {
      localStorage.clear()
      loadLocalStorageItems()
      message.success('已清空全部 LocalStorage，正在刷新页面...')
      setTimeout(() => {
        window.location.reload()
      }, 500)
    }
  })
}

const viewStorageDetail = (item) => {
  state.storageModalMode = 'view'
  state.modalStorageKey = item.key
  state.modalStorageValue = item.value
  state.storageModalVisible = true
}

const editStorageItem = (item) => {
  state.storageModalMode = 'edit'
  state.modalStorageKey = item.key
  state.modalStorageValue = item.value
  state.storageModalVisible = true
}

const openAddStorageModal = () => {
  state.storageModalMode = 'add'
  state.modalStorageKey = ''
  state.modalStorageValue = ''
  state.storageModalVisible = true
}

const formatModalValueJson = () => {
  try {
    const parsed = JSON.parse(state.modalStorageValue)
    state.modalStorageValue = JSON.stringify(parsed, null, 2)
    message.success('JSON 格式化成功')
  } catch (err) {
    message.warning(`无法解析为 JSON: ${err.message}`)
  }
}

const saveStorageModalValue = () => {
  const k = state.modalStorageKey.trim()
  if (!k) {
    message.error('键名 (Key) 不能为空')
    return
  }
  try {
    localStorage.setItem(k, state.modalStorageValue)
    loadLocalStorageItems()
    state.storageModalVisible = false
    message.success(`已保存存储项: ${k}`)
  } catch (err) {
    message.error(`保存失败: ${err.message}`)
  }
}

const reloadAllStores = async () => {
  try {
    await Promise.all([
      configStore.refreshConfig(),
      infoStore.loadInfoConfig(true),
      databaseStore.loadDatabases(),
      agentStore.initialize()
    ])
    loadLocalStorageItems()
    message.success('所有运行时 Store 数据已重新加载')
  } catch (err) {
    message.error(`重新加载失败: ${err.message}`)
  }
}
</script>

<style scoped lang="less">
.debug-panel-wrapper {
  display: flex;
  flex-direction: column;
  height: 95vh;
  max-height: 95vh;
  background: var(--gray-0);
  border-radius: 8px;
  overflow: hidden;

  &.fullscreen {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    width: 100vw;
    height: 100vh;
    border-radius: 0;
    z-index: 10000;
  }
}

/* 按钮通用重置与 Icon 完美居中 */
:deep(.ant-btn) {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 4px;
  border-radius: 4px;
  line-height: 1;
  vertical-align: middle;

  .ant-btn-icon,
  .anticon {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    line-height: 1;
    margin-right: 0 !important;
  }

  svg {
    display: inline-block;
    vertical-align: middle;
    flex-shrink: 0;
  }
}

/* 顶部单行 Header */
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 42px;
  padding: 0 14px;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-50);
  flex-shrink: 0;
}

.header-title-group {
  display: flex;
  align-items: center;
  gap: 8px;

  .main-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--gray-1000);
    letter-spacing: 0.2px;
  }

  .env-badge {
    font-size: 10px;
    line-height: 16px;
    padding: 0 6px;
    border-radius: 3px;
    background: var(--gray-200);
    color: var(--gray-700);
    font-family: 'Consolas', 'Monaco', monospace;
    font-weight: 500;
  }
}

.header-quick-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.debug-mode-switch-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);
  color: var(--gray-700);
  font-size: 11.5px;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;

  .switch-icon {
    color: var(--gray-600);
    display: inline-flex;
    align-items: center;
  }

  &:hover {
    border-color: var(--gray-500);
    color: var(--gray-1000);
    background: var(--gray-50);
  }

  &.active {
    background: var(--second-50);
    border-color: var(--second-300);
    color: var(--second-1000);
    font-weight: 600;

    .switch-icon {
      color: var(--second-1000);
    }

    &:hover {
      background: var(--second-100);
      border-color: var(--second-400);
      color: var(--second-1000);
    }
  }
}

:deep(.ant-btn.header-action-btn) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 4px;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);
  color: var(--gray-700);
  transition: all 0.15s ease;

  &:hover {
    border-color: var(--gray-500);
    color: var(--gray-1000);
    background: var(--gray-50);
  }

  &.close-btn:hover {
    color: var(--color-error-500);
    border-color: var(--color-error-100);
    background: var(--color-error-50);
  }
}

/* 主体布局 */
.panel-main-layout {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* 侧边导航栏 (更窄，深色 icon，灰色系) */
.panel-sidebar-nav {
  width: 140px;
  min-width: 140px;
  max-width: 140px;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-200);
  padding: 8px 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex-shrink: 0;

  .nav-tab-item {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 7px 8px;
    border-radius: 5px;
    font-size: 12px;
    color: var(--gray-700);
    cursor: pointer;
    transition: all 0.12s ease;
    user-select: none;

    /* 深色 Icon */
    .tab-icon {
      color: var(--gray-900);
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
    }

    .tab-label {
      flex: 1;
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .tab-badge {
      font-size: 10px;
      font-family: 'Consolas', 'Monaco', monospace;
      padding: 0 4px;
      border-radius: 8px;
      background: var(--gray-200);
      color: var(--gray-800);
      font-weight: 500;
    }

    &:hover {
      background: var(--gray-150);
      color: var(--gray-1000);

      .tab-icon {
        color: var(--gray-1000);
      }
    }

    &.active {
      background: var(--gray-200);
      color: var(--gray-1000);
      font-weight: 600;

      .tab-icon {
        color: var(--gray-1000);
      }
    }
  }
}

.panel-content-viewport {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 12px 16px;
  background: var(--gray-0);
}

.tab-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.pane-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 6px;
  }
}

.level-filter-chips {
  display: flex;
  gap: 5px;

  .level-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 7px;
    height: 26px;
    border-radius: 4px;
    font-size: 11px;
    font-family: 'Consolas', 'Monaco', monospace;
    cursor: pointer;
    border: 1px solid var(--gray-300);
    background: var(--gray-0);
    color: var(--gray-700);
    transition: all 0.12s ease;
    user-select: none;

    .level-indicator {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--gray-400);
      flex-shrink: 0;
    }

    &:hover {
      border-color: var(--gray-500);
      color: var(--gray-1000);
      background: var(--gray-50);
    }

    &.selected {
      border-color: var(--second-300);
      font-weight: 600;
      color: var(--second-1000);
      background: var(--second-50);

      &:hover {
        border-color: var(--second-400);
        color: var(--second-1000);
        background: var(--second-100);
      }

      &.level-info .level-indicator {
        background: var(--color-success-500);
      }
      &.level-warning .level-indicator {
        background: var(--color-warning-500);
      }
      &.level-error .level-indicator {
        background: var(--color-error-500);
      }
      &.level-debug .level-indicator {
        background: var(--gray-500);
      }
    }
  }
}

.log-search-input,
.config-search-input,
.storage-search-input {
  width: 210px;
}

.log-count-meta {
  font-size: 11px;
  color: var(--gray-500);
  font-family: 'Consolas', 'Monaco', monospace;
}

:deep(.ant-btn.tool-btn) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 26px;
  padding: 0 8px;
  font-size: 11.5px;
  border-radius: 4px;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);
  color: var(--gray-800);
  transition: all 0.15s ease;

  &:hover {
    border-color: var(--gray-500);
    color: var(--gray-1000);
    background: var(--gray-50);
  }

  &.icon-only {
    width: 26px;
    padding: 0;
  }

  &.ant-btn-primary,
  &.auto-refresh-btn.auto-refreshing {
    background: var(--second-50) !important;
    border-color: var(--second-300) !important;
    color: var(--second-1000) !important;

    &:hover {
      background: var(--second-100) !important;
      border-color: var(--second-400) !important;
      color: var(--second-1000) !important;
    }
  }

  &.danger {
    color: var(--color-error-700);
    border-color: var(--gray-300);

    &:hover {
      color: var(--color-error-500);
      border-color: var(--color-error-100);
      background: var(--color-error-50);
    }
  }
}

.copied-icon {
  color: var(--color-success-700);
}

/* ==================== TERMINAL LOG VIEWER ==================== */
.terminal-log-container {
  flex: 1;
  background: var(--gray-25);
  color: var(--gray-1000);
  border-radius: 6px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.45;
  overflow-y: auto;
  overflow-x: auto;
  padding: 8px 0;
  min-height: 200px;
  border: 1px solid var(--gray-200);

  &.wrap-lines .terminal-line {
    white-space: pre-wrap;
    word-break: break-all;
  }

  .terminal-lines {
    display: flex;
    flex-direction: column;
  }

  .terminal-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 2px 10px;
    white-space: nowrap;
    transition: background 0.1s ease;
    border-left: 2px solid transparent;

    &:hover {
      background: var(--gray-100);

      .line-copy-btn {
        opacity: 1;
      }
    }

    .line-no {
      width: 32px;
      color: var(--gray-500);
      text-align: right;
      flex-shrink: 0;
      user-select: none;
      font-size: 10px;
      padding-top: 1px;
    }

    .line-time {
      color: var(--gray-600);
      flex-shrink: 0;
      font-size: 11px;
    }

    .line-level {
      font-weight: 600;
      flex-shrink: 0;
      width: 48px;
      font-size: 11px;
    }

    .line-module {
      color: var(--gray-700);
      max-width: 140px;
      overflow: hidden;
      text-overflow: ellipsis;
      flex-shrink: 0;
    }

    .line-msg {
      flex: 1;
      overflow-x: auto;
      color: var(--gray-1000);
    }

    .line-copy-btn {
      opacity: 0;
      border: none;
      background: var(--gray-150);
      color: var(--gray-700);
      border-radius: 3px;
      padding: 1px 4px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: all 0.12s ease;

      &:hover {
        background: var(--gray-200);
        color: var(--gray-1000);
      }
    }

    &.level-info .line-level {
      color: var(--color-success-700);
    }
    &.level-warning .line-level {
      color: var(--color-warning-700);
    }
    &.level-error {
      background: var(--color-error-50);
      .line-level {
        color: var(--color-error-700);
      }
    }
    &.level-debug .line-level {
      color: var(--gray-400);
    }
  }

  .terminal-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 280px;
    color: var(--gray-600);
    gap: 8px;
  }
}

.log-error-banner {
  margin-top: 6px;
  padding: 5px 8px;
  border-radius: 4px;
  background: var(--color-error-50);
  border: 1px solid var(--color-error-100);
  color: var(--color-error-700);
  font-size: 11px;
}

/* ==================== CONFIG STYLES ==================== */
.view-mode-toggle {
  display: inline-flex;
  border: 1px solid var(--gray-300);
  border-radius: 4px;
  overflow: hidden;
  background: var(--gray-0);

  .mode-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 24px;
    padding: 0 8px;
    font-size: 11.5px;
    border: none;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
    transition: all 0.12s ease;

    &:hover {
      color: var(--gray-1000);
      background: var(--gray-50);
    }

    &.active {
      background: var(--second-50);
      color: var(--second-1000);
      font-weight: 600;

      &:hover {
        background: var(--second-100);
        color: var(--second-1000);
      }
    }
  }
}

.config-table-container,
.config-json-container,
.storage-table-container {
  flex: 1;
  overflow-y: auto;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
}

.diagnostic-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;

  th {
    background: var(--gray-50);
    color: var(--gray-700);
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    border-bottom: 1px solid var(--gray-200);
    font-size: 11px;
  }

  td {
    padding: 7px 10px;
    border-bottom: 1px solid var(--gray-150);
    color: var(--gray-800);
  }

  tr:hover td {
    background: var(--gray-50);
  }

  .code-cell {
    color: var(--gray-1000);
    font-weight: 500;
  }

  .type-pill {
    display: inline-block;
    padding: 1px 5px;
    font-size: 10px;
    font-family: 'Consolas', 'Monaco', monospace;
    border-radius: 3px;
    background: var(--gray-150);
    color: var(--gray-700);
  }

  .value-cell {
    word-break: break-all;
    max-width: 450px;
  }

  .desc-cell {
    color: var(--gray-500);
    font-size: 11px;
  }

  .empty-cell {
    text-align: center;
    padding: 30px;
    color: var(--gray-400);
  }
}

.json-code-box {
  margin: 0;
  padding: 12px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 11.5px;
  line-height: 1.45;
  background: var(--gray-50);
  color: var(--gray-1000);
  border-radius: 4px;
  overflow-x: auto;
}

/* ==================== USER TAB ==================== */
.user-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 14px;
}

.diagnostic-card {
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  overflow: hidden;

  .card-header {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 9px 12px;
    background: var(--gray-50);
    border-bottom: 1px solid var(--gray-200);

    .card-icon {
      color: var(--gray-900);
      display: inline-flex;
      align-items: center;
    }

    .card-title {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--gray-1000);
    }

    .badge-subtle {
      margin-left: auto;
      font-size: 10px;
      padding: 1px 5px;
      border-radius: 3px;
      background: var(--gray-200);
      color: var(--gray-700);
    }
  }

  .card-body {
    padding: 12px;
  }
}

.user-profile-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--gray-150);

  .user-title-box {
    display: flex;
    flex-direction: column;
    gap: 2px;

    .username-row {
      display: flex;
      align-items: center;
      gap: 6px;

      .username {
        font-size: 14px;
        font-weight: 600;
        color: var(--gray-1000);
      }

      .role-tag {
        font-size: 10px;
        padding: 0 5px;
        border-radius: 3px;
        background: var(--gray-200);
        color: var(--gray-800);
      }
    }

    .uid-row {
      font-size: 11px;
      color: var(--gray-500);
    }
  }
}

.info-kv-list {
  display: flex;
  flex-direction: column;
  gap: 6px;

  .kv-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11.5px;
    padding: 3px 0;
    border-bottom: 1px dashed var(--gray-150);

    &:last-child {
      border-bottom: none;
    }

    .k {
      color: var(--gray-500);
    }

    .v {
      color: var(--gray-900);
    }
  }
}

.user-switcher-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;

  .user-search-input {
    flex: 1;
  }
}

.user-list-scroll {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 5px;

  .user-item-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 8px;
    border-radius: 5px;
    border: 1px solid var(--gray-200);
    background: var(--gray-0);
    transition: all 0.12s ease;

    &:hover {
      background: var(--gray-50);
    }

    &.current {
      background: var(--gray-100);
      border-color: var(--gray-300);
    }

    .user-meta-left {
      display: flex;
      align-items: center;
      gap: 8px;

      .user-details {
        display: flex;
        flex-direction: column;

        .name {
          font-size: 12px;
          font-weight: 500;
          color: var(--gray-1000);
        }

        .sub {
          font-size: 10.5px;
          color: var(--gray-500);
        }
      }
    }

    .current-badge {
      font-size: 10.5px;
      color: var(--gray-500);
      font-weight: 500;
    }
  }

  .empty-hint {
    text-align: center;
    padding: 20px;
    color: var(--gray-400);
    font-size: 11.5px;
  }
}

/* ==================== LOCALSTORAGE TAB ==================== */
.storage-stats-pill {
  font-size: 11px;
  color: var(--gray-700);
  background: var(--gray-100);
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid var(--gray-200);
}

.storage-key-row {
  display: flex;
  align-items: center;
  gap: 5px;

  .key-name {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .badge-system {
    font-size: 9.5px;
    padding: 0 4px;
    border-radius: 2px;
    background: var(--gray-200);
    color: var(--gray-800);
    font-family: inherit;
  }
}

.storage-preview-text {
  color: var(--gray-700);
  display: inline-block;
  max-width: 480px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-actions-group {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;

  :deep(.ant-btn.table-action-btn) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border-radius: 4px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--gray-600);
    transition: all 0.12s ease;

    &:hover {
      background: var(--gray-150);
      color: var(--gray-1000);
      border-color: var(--gray-200);
    }

    &.danger:hover {
      background: var(--color-error-50);
      color: var(--color-error-500);
      border-color: var(--color-error-100);
    }
  }
}

.storage-bottom-cards {
  flex-shrink: 0;

  .debug-settings-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .setting-inline-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;

    .setting-text {
      display: flex;
      flex-direction: column;
      gap: 2px;

      .setting-name {
        font-size: 12px;
        font-weight: 500;
        color: var(--gray-1000);
      }

      .setting-desc {
        font-size: 11px;
        color: var(--gray-500);

        kbd {
          padding: 1px 4px;
          border-radius: 3px;
          border: 1px solid var(--gray-300);
          background: var(--gray-100);
          font-family: 'Consolas', 'Monaco', monospace;
          font-size: 10px;
          color: var(--gray-800);
        }
      }
    }
  }
}

:deep(.ant-switch.ant-switch-checked) {
  background: var(--second-600);

  &:hover:not(.ant-switch-disabled) {
    background: var(--second-700);
  }
}

/* LocalStorage Modal */
.storage-modal-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;

  .form-item {
    display: flex;
    flex-direction: column;
    gap: 5px;

    .form-label {
      font-size: 12px;
      font-weight: 500;
      color: var(--gray-700);
    }

    .label-with-action {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
  }

  .storage-value-textarea {
    font-size: 11.5px;
    line-height: 1.4;
  }

  .modal-footer-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 8px;
  }
}

.mt-12 {
  margin-top: 12px;
}

.font-mono {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
}

.text-muted {
  color: var(--gray-500);
}
</style>

<!-- 全局覆盖 a-modal 的 padding、高度与关闭按钮 -->
<style lang="less">
.ant-modal.debug-modal {
  max-width: 1400px;
  top: 2.5vh !important;
  padding-bottom: 0 !important;

  .ant-modal-content {
    padding: 0 !important;
    border-radius: 8px !important;
    overflow: hidden !important;
    height: 95vh !important;
    max-height: 95vh !important;
    display: flex !important;
    flex-direction: column !important;
    border: 1px solid var(--gray-300) !important;
    background: var(--gray-0) !important;
    box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.25) !important;
  }

  .ant-modal-body {
    padding: 0 !important;
    height: 100% !important;
    max-height: 100% !important;
    overflow: hidden !important;
    flex: 1 !important;
    display: flex !important;
    flex-direction: column !important;
  }

  .ant-modal-close {
    display: none !important;
  }
}
</style>
