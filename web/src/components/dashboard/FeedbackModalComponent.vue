<template>
  <!-- 反馈列表模态框 -->
  <a-modal v-model:open="modalVisible" title="用户反馈详情" width="1200px" :footer="null">
    <a-space style="margin-bottom: 16px">
      <a-segmented
        v-model:value="feedbackFilter"
        :options="feedbackOptions"
        @change="loadFeedbacks"
      />
    </a-space>

    <!-- 卡片列表 -->
    <div v-if="loadingFeedbacks" class="loading-container">
      <a-spin size="large" />
    </div>

    <div v-else class="feedback-cards-container">
      <div v-for="feedback in feedbacks" :key="feedback.id" class="feedback-card">
        <!-- 卡片头部：用户信息和反馈类型 -->
        <div class="card-header">
          <div class="user-info">
            <FallbackAvatar
              :src="feedback.avatar"
              :default-src="getFeedbackDefaultAvatarSrc(feedback)"
              :name="feedback.username"
              :seed="feedback.uid || feedback.username"
              kind="user"
              :size="32"
              shape="circle"
              :alt="feedback.username"
              class="user-avatar"
            />
            <div class="user-details">
              <div class="username">{{ feedback.username || '未知用户' }}</div>
            </div>
          </div>
          <a-tag
            :color="feedback.rating === 'like' ? 'green' : 'red'"
            class="rating-tag"
            size="small"
          >
            <template #icon>
              <LikeOutlined v-if="feedback.rating === 'like'" />
              <DislikeOutlined v-else />
            </template>
            {{ feedback.rating === 'like' ? '点赞' : '点踩' }}
          </a-tag>
        </div>

        <!-- 卡片内容：对话信息、消息内容和反馈原因 -->
        <div class="card-content">
          <!-- 对话标题 -->
          <div class="conversation-section" v-if="feedback.conversation_title">
            <div class="conversation-info">
              <div class="info-item">
                <span
                  class="conversation-title"
                  :class="{ collapsed: !expandedStates.get(`${feedback.id}-conversation`) }"
                >
                  标题：{{ feedback.conversation_title }}
                </span>
                <a-button
                  v-if="shouldShowConversationExpandButton(feedback.conversation_title)"
                  type="link"
                  size="small"
                  @click="toggleConversationExpand(feedback.id)"
                  class="expand-button-inline"
                >
                  {{ expandedStates.get(`${feedback.id}-conversation`) ? '收起' : '展开' }}
                </a-button>
              </div>
              <div class="info-item" v-if="!props.agentId">
                <span class="label">智能体:</span>
                <span class="value">{{ feedback.agent_id }}</span>
              </div>
            </div>
          </div>

          <!-- 消息内容 -->
          <div class="message-section">
            <div
              class="message-content"
              :class="{ collapsed: !expandedStates.get(`${feedback.id}-message`) }"
            >
              {{ feedback.message_content }}
            </div>
            <a-button
              v-if="shouldShowExpandButton(feedback.message_content)"
              type="link"
              size="small"
              @click="toggleExpand(feedback.id)"
              class="expand-button"
            >
              {{ expandedStates.get(`${feedback.id}-message`) ? '收起' : '展开全部' }}
            </a-button>
          </div>

          <!-- 反馈原因 -->
          <div v-if="feedback.reason" class="reason-section">
            <div class="reason-content">{{ feedback.reason }}</div>
          </div>
        </div>

        <!-- 卡片底部：时间信息 -->
        <div class="card-footer">
          <div class="time-info">
            <ClockCircleOutlined />
            <span>{{ formatFullDate(feedback.created_at) }}</span>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="feedbacks.length === 0" class="empty-state">
        <a-empty description="暂无反馈数据" />
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LikeOutlined, DislikeOutlined, ClockCircleOutlined } from '@ant-design/icons-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

// 常量配置
const CONFIG = {
  MESSAGE_MAX_LINES: 8, // 消息最大显示行数
  CONVERSATION_MAX_LINES: 2, // 对话标题最大显示行数
  CONVERSATION_MAX_CHARS: 60, // 对话标题字符数阈值
  AVG_CHARS_PER_LINE: 30 // 每行平均字符数（中英文混合）
}

// Props
const props = defineProps({
  agentId: {
    type: String,
    default: null
  }
})

// 模态框状态
const modalVisible = ref(false)

// 反馈相关数据
const feedbacks = ref([])
const loadingFeedbacks = ref(false)
const feedbackFilter = ref('all')
const feedbackOptions = [
  { label: '全部', value: 'all' },
  { label: '点赞', value: 'like' },
  { label: '点踩', value: 'dislike' }
]

// 展开状态映射（使用 Map 避免直接修改对象）
const expandedStates = ref(new Map())

// 显示模态框
const show = () => {
  modalVisible.value = true
  loadFeedbacks()
}

// 暴露方法给父组件
defineExpose({ show })

// 计算文本行数的辅助函数（估算）
const estimateLines = (text) => {
  if (!text) return 0
  return Math.ceil(text.length / CONFIG.AVG_CHARS_PER_LINE)
}

// 判断是否显示展开按钮
const shouldShowExpandButton = (content) => {
  return estimateLines(content) > CONFIG.MESSAGE_MAX_LINES
}

// 判断对话标题是否需要展开按钮
const shouldShowConversationExpandButton = (title) => {
  if (!title) return false
  return title.length > CONFIG.CONVERSATION_MAX_CHARS
}

// 切换展开/收起状态
const toggleExpand = (feedbackId) => {
  const key = `${feedbackId}-message`
  const currentState = expandedStates.value.get(key) ?? false
  expandedStates.value.set(key, !currentState)
}

// 切换对话标题展开/收起状态
const toggleConversationExpand = (feedbackId) => {
  const key = `${feedbackId}-conversation`
  const currentState = expandedStates.value.get(key) ?? false
  expandedStates.value.set(key, !currentState)
}

// 加载反馈列表
const loadFeedbacks = async () => {
  loadingFeedbacks.value = true
  try {
    const params = {
      rating: feedbackFilter.value === 'all' ? undefined : feedbackFilter.value,
      agent_id: props.agentId || undefined
    }

    const response = await dashboardApi.getFeedbacks(params)
    feedbacks.value = response
    // 重置展开状态
    expandedStates.value.clear()
  } catch (error) {
    console.error('加载反馈列表失败:', error)
    message.error('加载反馈列表失败，请稍后重试')
    feedbacks.value = []
  } finally {
    loadingFeedbacks.value = false
  }
}

const getFeedbackDefaultAvatarSrc = (feedback) => (feedback.uid ? generatePixelAvatar(feedback.uid) : '')

// 格式化完整日期
const formatFullDate = (dateString) => formatFullDateTime(dateString)

// 监听 agentId 变化，重新加载数据
watch(
  () => props.agentId,
  () => {
    if (modalVisible.value) {
      loadFeedbacks()
    }
  }
)
</script>

<style scoped lang="less">
// 加载状态
.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px 0;
}

// 卡片容器 - 自适应多列布局
.feedback-cards-container {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  max-height: 600px;
  overflow-y: auto;
  padding-right: 8px;

  // 滚动条样式
  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: var(--gray-100);
    border-radius: 3px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--gray-300);
    border-radius: 3px;

    &:hover {
      background: var(--gray-400);
    }
  }
}

// 反馈卡片 - 紧凑设计
.feedback-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;

  &:hover {
    border-color: var(--main-color);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }
}

// 卡片头部 - 紧凑
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-25);
  border-radius: 8px 8px 0 0;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-avatar {
  flex-shrink: 0;
}

.user-details {
  .username {
    font-weight: 500;
    color: var(--gray-900);
    font-size: 13px;
    line-height: 1.2;
  }
}

.rating-tag {
  font-weight: 500;
  font-size: 11px;
}

// 卡片内容 - 紧凑
.card-content {
  padding: 16px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message-section {
  flex: 1;
}

.message-content {
  background: var(--gray-50);
  padding: 10px;
  border-radius: 6px;
  // border-left: 3px solid var(--main-color);
  font-size: 13px;
  line-height: 1.4;
  color: var(--gray-800);
  word-break: break-word;
  overflow: hidden;
  transition: max-height 0.3s ease;
}

.message-content.collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  line-clamp: 8;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expand-button {
  padding: 0;
  height: auto;
  font-size: 12px;
  margin-top: 8px;
  color: var(--main-color);
}

.conversation-section {
  margin: 0;
}

.conversation-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-item {
  display: flex;
  align-items: center;
  font-size: 12px;

  .label {
    color: var(--gray-600);
    margin-right: 6px;
    min-width: 50px;
    font-weight: 500;
  }

  .value {
    color: var(--gray-800);
    font-weight: 400;
    word-break: break-all;
  }

  // 对话标题样式（独立显示）
  .conversation-title {
    display: block;
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
    line-height: 1.4;
    word-break: break-word;
    transition: all 0.3s ease;

    &.collapsed {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  // 包含对话标题的 info-item 改为垂直布局
  &:has(.conversation-title) {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
}

.expand-button-inline {
  padding: 0;
  height: auto;
  font-size: 11px;
  color: var(--main-color);
  align-self: flex-start;
}

.reason-section {
  margin: 0;
}

.reason-content {
  background: var(--color-warning-50);
  padding: 10px;
  border-radius: 6px;
  border-left: 3px solid var(--color-warning-500);
  font-size: 13px;
  line-height: 1.4;
  color: var(--gray-800);
  word-break: break-word;
}

// 卡片底部 - 紧凑
.card-footer {
  padding: 8px 16px;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-25);
  border-radius: 0 0 8px 8px;
}

.time-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--gray-500);
}

// 空状态
.empty-state {
  grid-column: 1 / -1;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 60px 0;
}

// 响应式设计
@media (max-width: 768px) {
  .feedback-cards-container {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .card-header {
    padding: 10px 12px;
    gap: 8px;
  }

  .card-content {
    padding: 12px;
    gap: 10px;
  }

  .card-footer {
    padding: 6px 12px;
  }
}

@media (max-width: 480px) {
  .feedback-cards-container {
    gap: 8px;
  }

  .card-header {
    padding: 8px 10px;
  }

  .card-content {
    padding: 10px;
    gap: 8px;
  }

  .message-content,
  .reason-content {
    padding: 8px;
    font-size: 12px;
  }

  .info-item {
    font-size: 11px;
  }
}
</style>
