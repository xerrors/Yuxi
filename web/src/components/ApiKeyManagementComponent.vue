<template>
  <div class="apikey-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">API Key 管理</div>
        <p class="section-description">
          创建和管理 API Key，用于外部系统调用 Agent 对话接口。密钥仅显示一次，请妥善保管。
        </p>
      </div>
      <a-button type="primary" @click="showCreateModal" class="add-btn lucide-icon-btn">
        <Plus :size="14" />
        创建 API Key
      </a-button>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="loading">
        <div v-if="error" class="error-message">
          <a-alert type="error" :message="error" show-icon />
        </div>

        <div class="cards-container">
          <div v-if="apiKeys.length === 0" class="empty-state">
            <a-empty description="暂无 API Key，点击上方按钮创建一个" />
          </div>
          <div v-else class="apikey-cards-grid">
            <div v-for="key in apiKeys" :key="key.id" class="apikey-card">
              <div class="card-header">
                <div class="key-info">
                  <KeyIcon size="18" class="key-icon" />
                  <div class="key-info-content">
                    <h4 class="key-name">{{ key.name }}</h4>
                  </div>
                </div>
                <code class="key-prefix">{{ key.key_prefix }}****</code>
              </div>

              <div class="card-content">
                <div class="info-item">
                  <span class="info-label">过期时间:</span>
                  <span class="info-value">{{ key.expires_at || '永不过期' }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">最后使用:</span>
                  <span class="info-value">{{ formatTime(key.last_used_at) }}</span>
                </div>
              </div>

              <div class="card-footer">
                <div class="footer-left">
                  <span class="switch-label">{{ key.is_enabled ? '已启用' : '已禁用' }}</span>
                  <a-switch :checked="key.is_enabled" size="small" @change="toggleEnabled(key)" />
                </div>
                <div class="footer-actions">
                  <a-tooltip title="重新生成（获取完整密钥）">
                    <a-button
                      type="text"
                      size="small"
                      @click="regenerateKey(key)"
                      class="action-btn lucide-icon-btn"
                    >
                      <RefreshCw :size="14" />
                      <span>重新生成</span>
                    </a-button>
                  </a-tooltip>
                  <a-popconfirm
                    title="确定要删除此 API Key 吗？此操作不可恢复。"
                    @confirm="deleteKey(key)"
                    ok-text="确定"
                    cancel-text="取消"
                  >
                    <a-tooltip title="删除">
                      <a-button type="text" size="small" danger class="action-btn lucide-icon-btn">
                        <Trash2 :size="14" />
                        <span>删除</span>
                      </a-button>
                    </a-tooltip>
                  </a-popconfirm>
                </div>
              </div>
            </div>
          </div>
        </div>
      </a-spin>
    </div>

    <!-- 创建 Modal -->
    <a-modal
      v-model:open="createModalVisible"
      title="创建 API Key"
      @ok="handleCreate"
      :confirmLoading="createLoading"
      ok-text="创建"
      cancel-text="取消"
    >
      <a-form layout="vertical" :model="createForm">
        <a-form-item label="名称" required>
          <a-input v-model:value="createForm.name" placeholder="如：生产环境API" />
        </a-form-item>
        <a-form-item label="过期时间">
          <a-date-picker
            v-model:value="createForm.expires_at"
            show-time
            placeholder="留空表示永不过期"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 密钥显示 Modal (创建后一次性显示) -->
    <a-modal
      v-model:open="secretModalVisible"
      title="API Key 已创建"
      :closable="true"
      @cancel="secretModalVisible = false"
      :footer="null"
      width="520px"
    >
      <div class="secret-display">
        <a-alert
          type="warning"
          message="请立即复制密钥，关闭后将无法再次查看完整密钥"
          show-icon
          class="secret-alert"
        />
        <div class="secret-value-container">
          <code class="secret-value">{{ createdSecret }}</code>
          <a-button type="primary" @click="copySecret" class="copy-btn lucide-icon-btn">
            <Copy :size="14" />
            复制
          </a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, Copy } from 'lucide-vue-next'
import { Key as KeyIcon } from 'lucide-vue-next'
import { apikeyApi } from '@/apis/apikey_api'

const loading = ref(false)
const error = ref(null)
const apiKeys = ref([])

const createModalVisible = ref(false)
const secretModalVisible = ref(false)
const createLoading = ref(false)
const createdSecret = ref('')

const createForm = reactive({
  name: '',
  expires_at: null
})

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const loadApiKeys = async () => {
  loading.value = true
  error.value = null
  try {
    const res = await apikeyApi.list()
    apiKeys.value = res.api_keys || []
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const showCreateModal = () => {
  createForm.name = ''
  createForm.expires_at = null
  createModalVisible.value = true
}

const handleCreate = async () => {
  if (!createForm.name.trim()) {
    message.error('请输入名称')
    return
  }

  createLoading.value = true
  try {
    const data = { name: createForm.name }
    if (createForm.expires_at) {
      data.expires_at = createForm.expires_at.format('YYYY-MM-DDTHH:mm:ss')
    }

    const res = await apikeyApi.create(data)
    createdSecret.value = res.secret
    createModalVisible.value = false
    secretModalVisible.value = true
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '创建失败')
  } finally {
    createLoading.value = false
  }
}

const copySecret = async () => {
  try {
    await navigator.clipboard.writeText(createdSecret.value)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

const regenerateKey = async (key) => {
  try {
    const res = await apikeyApi.regenerate(key.id)
    createdSecret.value = res.secret
    secretModalVisible.value = true
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '重新生成失败')
  }
}

const toggleEnabled = async (key) => {
  try {
    await apikeyApi.update(key.id, { is_enabled: !key.is_enabled })
    message.success(key.is_enabled ? '已禁用' : '已启用')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '操作失败')
  }
}

const deleteKey = async (key) => {
  try {
    await apikeyApi.delete(key.id)
    message.success('删除成功')
    await loadApiKeys()
  } catch (e) {
    message.error(e.message || '删除失败')
  }
}

onMounted(() => {
  loadApiKeys()
})
</script>

<style lang="less" scoped>
.apikey-management {
  .content-section {
    .error-message {
      margin-bottom: 16px;
    }

    .cards-container {
      .empty-state {
        padding: 48px 0;
      }

      .apikey-cards-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 12px;
      }

      .apikey-card {
        background: var(--gray-0);
        border: 1px solid var(--gray-150);
        border-radius: 8px;
        padding: 12px;
        transition:
          border-color 0.2s,
          box-shadow 0.2s;

        &:hover {
          border-color: var(--gray-300);
        }

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 10px;

          .key-info {
            display: flex;
            align-items: center;
            gap: 10px;

            .key-icon {
              color: var(--main-600);
              flex-shrink: 0;
            }

            .key-info-content {
              .key-name {
                font-size: 14px;
                font-weight: 600;
                color: var(--gray-900);
                margin: 0;
              }
            }
          }

          .key-prefix {
            font-family: 'Monaco', 'Consolas', monospace;
            font-size: 12px;
            color: var(--gray-600);
            background: var(--gray-50);
            padding: 2px 8px;
            border-radius: 8px;
          }
        }

        .card-content {
          margin-bottom: 10px;

          .info-item {
            display: flex;
            align-items: flex-start;
            gap: 6px;
            margin-bottom: 6px;
            font-size: 13px;

            &:last-child {
              margin-bottom: 0;
            }

            .info-label {
              color: var(--gray-600);
              flex-shrink: 0;
            }

            .info-value {
              color: var(--gray-900);
              word-break: break-all;
            }

            &.half {
              flex: 1;
            }
          }
        }

        .card-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding-top: 8px;
          border-top: 1px solid var(--gray-100);

          .footer-left {
            display: flex;
            align-items: center;
            gap: 8px;

            .switch-label {
              font-size: 12px;
              color: var(--gray-600);
            }
          }

          .footer-actions {
            display: flex;
            gap: 4px;
          }

          .action-btn {
            font-size: 12px;
            color: var(--gray-700);
            display: inline-flex;
            align-items: center;
            gap: 4px;

            &:hover {
              color: var(--main-600);
            }
          }
        }
      }
    }
  }
}

.secret-display {
  .secret-alert {
    margin-bottom: 16px;
  }

  .secret-value-container {
    display: flex;
    gap: 8px;
    align-items: stretch;

    .secret-value {
      flex: 1;
      font-family: 'Monaco', 'Consolas', monospace;
      font-size: 13px;
      background: var(--gray-100);
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      padding: 12px;
      word-break: break-all;
      color: var(--gray-900);
    }

    .copy-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
  }
}
</style>
