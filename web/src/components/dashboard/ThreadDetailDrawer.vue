<template>
  <a-drawer
    v-model:open="visible"
    title="会话详情与交互审计"
    placement="right"
    :width="drawerWidth"
    :body-style="{ padding: '0px', display: 'flex', flexDirection: 'column', height: '100%' }"
    @close="handleClose"
  >
    <template #extra>
      <a-tag v-if="detail?.status === 'active'" color="green">进行中</a-tag>
      <a-tag v-else-if="detail?.status === 'archived'" color="blue">已归档</a-tag>
      <a-tag v-else color="default">{{ detail?.status || '未知' }}</a-tag>
    </template>

    <div v-if="loading" class="drawer-loading">
      <a-spin size="large" tip="正在加载会话流水..." />
    </div>

    <div v-else-if="detail" class="drawer-content">
      <!-- 顶部会话元数据摘要 -->
      <div class="thread-meta-banner">
        <div class="meta-main-title">
          <div class="thread-title-text">{{ detail.title || '未命名会话' }}</div>
          <a-tag v-if="detail.is_pinned" color="orange" class="pinned-tag">已置顶</a-tag>
        </div>

        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-label">Thread ID</span>
            <span class="meta-value code-font" :title="detail.thread_id">{{
              detail.thread_id
            }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">所属智能体</span>
            <span class="meta-value font-medium" :title="detail.agent_id">
              {{ detail.agent_name || detail.agent_id }}
              <a-tag v-if="detail.agent_deleted" class="history-tag">已删除</a-tag>
            </span>
          </div>
          <div class="meta-item">
            <span class="meta-label">所属用户</span>
            <span class="meta-value" :title="detail.uid">
              {{ detail.username || detail.uid }}
              <a-tag v-if="detail.user_deleted" class="history-tag">已注销</a-tag>
            </span>
          </div>
          <div class="meta-item">
            <span class="meta-label">消息总数</span>
            <span class="meta-value font-semibold">{{ detail.message_count }} 条</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Token 消耗</span>
            <span class="meta-value font-semibold text-primary">
              {{ (detail.total_tokens || 0).toLocaleString() }}
            </span>
          </div>
          <div class="meta-item">
            <span class="meta-label">更新时间</span>
            <span class="meta-value">{{ formatTime(detail.updated_at) }}</span>
          </div>
        </div>
      </div>

      <!-- 消息流水列表 -->
      <div class="messages-timeline-container">
        <div class="timeline-header">
          <span class="timeline-title">消息时间线 ({{ detail.messages?.length || 0 }})</span>
          <span class="timeline-hint">按交互顺序完整记录请求与工具调用</span>
        </div>

        <div v-if="!detail.messages || detail.messages.length === 0" class="empty-messages">
          <a-empty description="该会话尚无消息记录" />
        </div>

        <div v-else class="messages-list">
          <div
            v-for="(msg, index) in detail.messages"
            :key="msg.id || index"
            class="message-card"
            :class="[`role-${msg.role}`]"
          >
            <!-- 消息头部 -->
            <div class="message-header">
              <div class="sender-info">
                <div class="role-badge" :class="msg.role">
                  <User v-if="msg.role === 'user'" class="role-icon" />
                  <Bot v-else-if="msg.role === 'assistant'" class="role-icon" />
                  <Wrench v-else-if="msg.role === 'tool'" class="role-icon" />
                  <Cpu v-else class="role-icon" />
                </div>
                <span class="role-name">{{ getRoleLabel(msg.role) }}</span>
                <span class="message-time">{{ formatTime(msg.created_at) }}</span>
              </div>

              <div class="msg-meta-tags">
                <a-tag v-if="msg.token_count" size="small" class="token-tag">
                  {{ msg.token_count }} tokens
                </a-tag>
                <a-tag
                  v-if="msg.message_type && msg.message_type !== 'text'"
                  size="small"
                  color="purple"
                >
                  {{ msg.message_type }}
                </a-tag>
              </div>
            </div>

            <!-- 消息正文 -->
            <div class="message-body">
              <div class="message-text">{{ msg.content }}</div>

              <!-- 工具调用折叠区域 -->
              <div v-if="msg.tool_calls && msg.tool_calls.length > 0" class="tool-calls-container">
                <div class="tool-calls-title">
                  <Wrench class="icon-inline" /> 工具执行记录 ({{ msg.tool_calls.length }})
                </div>

                <div
                  v-for="(tc, tcIdx) in msg.tool_calls"
                  :key="tc.id || tcIdx"
                  class="tool-call-item"
                  :class="tc.status"
                >
                  <div
                    class="tool-call-summary"
                    @click="toggleToolExpand(tc.id || `${index}-${tcIdx}`)"
                  >
                    <div class="tool-name-wrap">
                      <span class="tool-name">{{ tc.tool_name }}</span>
                      <a-tag
                        :color="
                          tc.status === 'success'
                            ? 'green'
                            : tc.status === 'error'
                              ? 'red'
                              : 'orange'
                        "
                        size="small"
                      >
                        {{
                          tc.status === 'success'
                            ? '成功'
                            : tc.status === 'error'
                              ? '失败'
                              : '执行中'
                        }}
                      </a-tag>
                    </div>
                    <span class="tool-expand-arrow">
                      {{ isToolExpanded(tc.id || `${index}-${tcIdx}`) ? '收起' : '展开详情' }}
                    </span>
                  </div>

                  <div
                    v-if="isToolExpanded(tc.id || `${index}-${tcIdx}`)"
                    class="tool-call-details"
                  >
                    <div v-if="tc.tool_input" class="tool-detail-section">
                      <div class="detail-label">输入参数 (Input):</div>
                      <pre class="json-code">{{ formatJson(tc.tool_input) }}</pre>
                    </div>

                    <div v-if="tc.tool_output" class="tool-detail-section">
                      <div class="detail-label">执行结果 (Output):</div>
                      <pre class="output-code">{{ tc.tool_output }}</pre>
                    </div>

                    <div v-if="tc.error_message" class="tool-detail-section error-section">
                      <div class="detail-label text-error">错误信息 (Error):</div>
                      <div class="error-msg">{{ tc.error_message }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </a-drawer>
</template>

<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { User, Bot, Wrench, Cpu } from '@lucide/vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const visible = ref(false)
const loading = ref(false)
const detail = ref(null)
const expandedTools = ref(new Set())

const drawerWidth = 'min(720px, 100vw)'

const open = async (threadId) => {
  if (!threadId) return
  visible.value = true
  loading.value = true
  detail.value = null
  expandedTools.value.clear()

  try {
    const data = await dashboardApi.getConversationDetail(threadId)
    detail.value = data
  } catch (err) {
    console.error('获取会话详情失败:', err)
    message.error('获取会话详情失败')
    visible.value = false
  } finally {
    loading.value = false
  }
}

const handleClose = () => {
  visible.value = false
  detail.value = null
  expandedTools.value.clear()
}

const getRoleLabel = (role) => {
  const map = {
    user: '用户',
    assistant: '智能体',
    tool: '工具返回',
    system: '系统提示'
  }
  return map[role] || role
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  return formatFullDateTime(timeStr)
}

const formatJson = (obj) => {
  if (typeof obj === 'string') {
    try {
      return JSON.stringify(JSON.parse(obj), null, 2)
    } catch {
      return obj
    }
  }
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

const toggleToolExpand = (id) => {
  if (expandedTools.value.has(id)) {
    expandedTools.value.delete(id)
  } else {
    expandedTools.value.add(id)
  }
}

const isToolExpanded = (id) => expandedTools.value.has(id)

defineExpose({
  open
})
</script>

<style scoped lang="less">
.drawer-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 300px;
}

.drawer-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  background-color: var(--gray-25);
}

.thread-meta-banner {
  padding: 20px 24px;
  background: var(--gray-0);
  border-bottom: 1px solid var(--gray-200);

  .meta-main-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;

    .thread-title-text {
      font-size: 17px;
      font-weight: 600;
      color: var(--gray-1000);
      word-break: break-all;
    }
  }

  .meta-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px 16px;

    .meta-item {
      display: flex;
      flex-direction: column;
      gap: 2px;

      .meta-label {
        font-size: 11px;
        color: var(--gray-500);
        text-transform: uppercase;
        font-weight: 500;
      }

      .meta-value {
        font-size: 13px;
        color: var(--gray-800);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;

        &.code-font {
          font-family: monospace;
          font-size: 12px;
          color: var(--gray-700);
        }

        &.text-primary {
          color: var(--main-color);
        }

        .history-tag {
          margin-left: 4px;
          border-color: var(--gray-150);
          background: var(--gray-100);
          color: var(--gray-600);
          font-size: 10px;
        }
      }
    }
  }
}

.messages-timeline-container {
  padding: 20px 24px;
  flex: 1;

  .timeline-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 16px;

    .timeline-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-900);
    }

    .timeline-hint {
      font-size: 12px;
      color: var(--gray-500);
    }
  }

  .empty-messages {
    padding: 40px 0;
    text-align: center;
  }
}

.messages-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 16px;
  transition: all 0.2s ease;

  &:hover {
    box-shadow: 0 2px 6px var(--shadow-100);
  }

  &.role-user {
    border-left: 3px solid var(--color-info-500);
  }

  &.role-assistant {
    border-left: 3px solid var(--main-color);
  }

  &.role-tool {
    border-left: 3px solid var(--color-warning-500);
    background: var(--gray-50);
  }

  .message-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;

    .sender-info {
      display: flex;
      align-items: center;
      gap: 8px;

      .role-badge {
        width: 26px;
        height: 26px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--gray-100);
        color: var(--gray-600);

        &.user {
          background: var(--color-info-50);
          color: var(--color-info-600);
        }

        &.assistant {
          background: var(--main-20);
          color: var(--main-color);
        }

        &.tool {
          background: var(--color-warning-50);
          color: var(--color-warning-600);
        }

        .role-icon {
          width: 14px;
          height: 14px;
        }
      }

      .role-name {
        font-size: 13px;
        font-weight: 600;
        color: var(--gray-900);
      }

      .message-time {
        font-size: 11px;
        color: var(--gray-500);
      }
    }

    .msg-meta-tags {
      display: flex;
      gap: 6px;

      .token-tag {
        font-size: 11px;
        background: var(--gray-100);
        color: var(--gray-600);
        border: none;
      }
    }
  }

  .message-body {
    .message-text {
      font-size: 13px;
      line-height: 1.6;
      color: var(--gray-900);
      white-space: pre-wrap;
      word-break: break-word;
    }
  }
}

.tool-calls-container {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--gray-200);

  .tool-calls-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--gray-700);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 4px;

    .icon-inline {
      width: 13px;
      height: 13px;
    }
  }

  .tool-call-item {
    background: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    margin-bottom: 8px;
    overflow: hidden;

    &.error {
      border-color: var(--color-error-200);
    }

    .tool-call-summary {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease;

      &:hover {
        background: var(--gray-100);
      }

      .tool-name-wrap {
        display: flex;
        align-items: center;
        gap: 8px;

        .tool-name {
          font-family: monospace;
          font-size: 12px;
          font-weight: 600;
          color: var(--gray-900);
        }
      }

      .tool-expand-arrow {
        font-size: 11px;
        color: var(--main-color);
      }
    }

    .tool-call-details {
      padding: 10px 12px;
      border-top: 1px solid var(--gray-200);
      background: var(--gray-0);

      .tool-detail-section {
        margin-bottom: 8px;

        &:last-child {
          margin-bottom: 0;
        }

        .detail-label {
          font-size: 11px;
          font-weight: 600;
          color: var(--gray-600);
          margin-bottom: 4px;
        }

        .json-code,
        .output-code {
          font-family: monospace;
          font-size: 11px;
          background: var(--gray-50);
          padding: 8px;
          border-radius: 4px;
          border: 1px solid var(--gray-200);
          max-height: 200px;
          overflow: auto;
          white-space: pre-wrap;
          word-break: break-all;
          color: var(--gray-800);
        }

        .error-msg {
          font-size: 12px;
          color: var(--color-error-600);
          background: var(--color-error-50);
          padding: 8px;
          border-radius: 4px;
          border: 1px solid var(--color-error-200);
        }
      }
    }
  }
}

@media (max-width: 768px) {
  .thread-meta-banner {
    padding: 16px;

    .meta-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  .messages-timeline-container {
    padding: 16px;
  }
}
</style>
