<template>
  <div
    v-if="message.message_type === 'multimodal_image' && message.image_content"
    class="message-image"
  >
    <img :src="`data:image/jpeg;base64,${message.image_content}`" alt="用户上传的图片" />
  </div>
  <div
    ref="messageRef"
    class="message-box"
    :class="[
      message.type,
      customClasses,
      { 'is-stuck': isStuck, 'is-shrunk': isStuck && isShrunkRight }
    ]"
    :data-msg-id="message.id"
  >
    <!-- 用户提问内容及右下角动作条 -->
    <div
      v-if="message.type === 'human'"
      class="human-message-wrapper"
      :class="{
        'is-stuck-layout': isStuck && isMultiLine,
        'is-collapsed': isStuck && isMultiLine && !isExpanded,
        'is-shrunk-right': isStuck && isShrunkRight
      }"
    >
      <!-- 向右收缩后的悬浮胶囊/球 -->
      <div
        v-if="isStuck && isShrunkRight"
        class="stuck-shrunk-ball"
        @click.stop="isShrunkRight = false"
        title="展开提问"
      >
        <MessageSquare size="15" />
        <ChevronLeft size="12" class="arrow-hint" />
      </div>

      <!-- 正常的提问卡片内容 -->
      <template v-else>
        <p
          ref="humanTextRef"
          class="message-text render-html"
          @click="handleMessageClick"
          v-html="renderUserMessage(message.content)"
        ></p>
        <div class="human-action-bar">
          <!-- 内联复制按钮 (默认不显示，hover 显现) -->
          <div
            class="message-copy-btn inline-copy"
            @click.stop="copyToClipboard(message.content)"
            :class="{ 'is-copied': isCopied }"
            title="复制问题"
          >
            <Check v-if="isCopied" size="13" />
            <Copy v-else size="13" />
          </div>
          <!-- 左右收起按钮 (仅在吸顶且多行时可用) -->
          <div
            v-if="isStuck && isMultiLine"
            class="action-icon-btn shrink-right-btn"
            @click.stop="isShrunkRight = true"
            title="向右收起"
          >
            <ChevronRight size="13" />
          </div>
          <!-- 上下折叠/展开纯图标按钮 (仅在吸顶且多行时可用) -->
          <div
            v-if="isStuck && isMultiLine"
            class="action-icon-btn expand-toggle-btn"
            @click.stop="isExpanded = !isExpanded"
            :title="isExpanded ? '折叠' : '展开'"
          >
            <ChevronUp v-if="isExpanded" size="13" />
            <ChevronDown v-else size="13" />
          </div>
        </div>
      </template>
    </div>

    <p v-else-if="message.type === 'system'" class="message-text-system">{{ message.content }}</p>

    <!-- 助手消息 -->
    <div v-else-if="message.type === 'ai'" class="assistant-message">
      <div v-if="parsedData.reasoning_content" class="reasoning-box">
        <a-collapse v-model:activeKey="reasoningActiveKey" :bordered="false">
          <template #expandIcon="{ isActive }">
            <caret-right-outlined :rotate="isActive ? 90 : 0" />
          </template>
          <a-collapse-panel
            key="show"
            :header="message.status == 'reasoning' ? '正在思考...' : '推理过程'"
            class="reasoning-header"
          >
            <p class="reasoning-content">{{ parsedData.reasoning_content }}</p>
          </a-collapse-panel>
        </a-collapse>
      </div>

      <!-- 消息内容 -->
      <MarkdownPreview
        v-if="parsedData.content"
        :key="message.id"
        :content="parsedData.content"
        class="message-md"
      />

      <div v-else-if="parsedData.reasoning_content" class="empty-block"></div>

      <!-- 错误提示块 -->
      <div v-if="displayError" class="error-hint">
        <span v-if="getErrorMessage">{{ getErrorMessage }}</span>
        <span v-else-if="message.error_type === 'interrupted'">回答生成已中断</span>
        <span v-else-if="message.error_type === 'unexpect'">生成过程中出现异常</span>
        <span v-else-if="message.error_type === 'content_guard_blocked'"
          >检测到敏感内容，已中断输出</span
        >
        <span v-else>{{ message.error_type || '未知错误' }}</span>
      </div>

      <ToolCallsGroupComponent
        v-if="!hideToolCalls && validToolCalls.length > 0"
        :tool-calls="validToolCalls"
      />

      <div v-if="message.isStoppedByUser" class="retry-hint">
        你停止生成了本次回答
        <span class="retry-link" @click="emit('retryStoppedMessage', message.id)"
          >重新编辑问题</span
        >
      </div>

      <div
        v-if="
          (message.role == 'received' || message.role == 'assistant') &&
          message.status == 'finished' &&
          showRefs
        "
      >
        <RefsComponent
          :message="message"
          :show-refs="showRefs"
          :is-latest-message="isLatestMessage"
          :sources="messageSources"
          @retry="emit('retry')"
          @openRefs="emit('openRefs', $event)"
        />
      </div>
      <!-- 错误消息 -->
    </div>

    <div v-if="infoStore.debugMode" class="status-info">{{ message }}</div>

    <!-- 自定义内容 -->
    <slot></slot>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { CaretRightOutlined } from '@ant-design/icons-vue'
import RefsComponent from '@/components/RefsComponent.vue'
import { Copy, Check, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, MessageSquare } from 'lucide-vue-next'
import ToolCallsGroupComponent from '@/components/ToolCallsGroupComponent.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { useAgentStore } from '@/stores/agent'
import { useInfoStore } from '@/stores/info'
import { storeToRefs } from 'pinia'
import { MessageProcessor } from '@/utils/messageProcessor'

const props = defineProps({
  // 消息角色：'user'|'assistant'|'sent'|'received'
  message: {
    type: Object,
    required: true
  },
  // 是否正在处理中
  isProcessing: {
    type: Boolean,
    default: false
  },
  // 自定义类
  customClasses: {
    type: Object,
    default: () => ({})
  },
  // 是否显示推理过程
  showRefs: {
    type: [Array, Boolean],
    default: () => false
  },
  // 是否为最新消息
  isLatestMessage: {
    type: Boolean,
    default: false
  },
  hideToolCalls: {
    type: Boolean,
    default: false
  },
  // 是否显示调试信息 (已废弃，使用 infoStore.debugMode)
  debugMode: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['retry', 'retryStoppedMessage', 'openRefs'])

// 复制状态
const isCopied = ref(false)

// 原地吸顶悬浮与多行折叠控制
const messageRef = ref(null)
const humanTextRef = ref(null)
const isStuck = ref(false)
const isMultiLine = ref(false)
const isExpanded = ref(false)
const isShrunkRight = ref(false)
let stickyObserver = null

// 触顶状态退出时还原折叠和右收缩状态
watch(isStuck, (newVal) => {
  if (!newVal) {
    isShrunkRight.value = false
    isExpanded.value = false
  }
})

// 初始化/重新建立吸顶监听与多行测量
const initStickyObserver = () => {
  if (stickyObserver) {
    stickyObserver.disconnect()
    stickyObserver = null
  }

  // 1. 监测是否是多行提问以决定是否需要吸顶折叠
  if (humanTextRef.value) {
    setTimeout(() => {
      if (humanTextRef.value && humanTextRef.value.scrollHeight > 28) {
        isMultiLine.value = true
      } else {
        isMultiLine.value = false
      }
    }, 100)
  }

  // 2. 监测气泡是否触顶悬浮
  if (messageRef.value) {
    const scrollContainer = messageRef.value.closest('.chat-main')
    if (scrollContainer) {
      stickyObserver = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            const rect = entry.boundingClientRect
            const rootRect = entry.rootBounds
            if (rootRect) {
              // 判断元素顶部是否处于或滑过 top: 12px 触顶线（因 top: 12px，故在 13px 以内触发），且元素底部仍在视口内
              const isElementStuck = rect.top <= rootRect.top + 13 && rect.bottom > rootRect.top
              isStuck.value = isElementStuck
            }
          }
        },
        {
          root: scrollContainer,
          rootMargin: '-12px 0px 0px 0px',
          threshold: [0.99, 1.0]
        }
      )
      stickyObserver.observe(messageRef.value)
    }
  }
}

onMounted(() => {
  if (props.message.type === 'human') {
    initStickyObserver()
  }
})

// 关键修复：当对话中的 message 被替换复用时，我们需要通过监测 props.message.id 动态重连 IntersectionObserver
watch(
  () => props.message.id,
  () => {
    nextTick(() => {
      if (props.message.type === 'human') {
        initStickyObserver()
      }
    })
  }
)

onUnmounted(() => {
  if (stickyObserver) {
    stickyObserver.disconnect()
    stickyObserver = null
  }
})

const copyToClipboard = async (text) => {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      // 降级处理：使用传统的 execCommand 方法
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.position = 'fixed'
      textArea.style.left = '-999999px'
      textArea.style.top = '-999999px'
      document.body.appendChild(textArea)
      textArea.focus()
      textArea.select()
      const successful = document.execCommand('copy')
      document.body.removeChild(textArea)
      if (!successful) throw new Error('execCommand failed')
    }
    isCopied.value = true
    setTimeout(() => {
      isCopied.value = false
    }, 2000)
  } catch (err) {
    console.error('Failed to copy: ', err)
  }
}

// 推理面板展开状态
const reasoningActiveKey = ref(['hide'])

// 错误消息处理
const displayError = computed(() => {
  // 简化错误判断：只检查明确的错误类型标识
  return !!(props.message.error_type || props.message.extra_metadata?.error_type)
})

const getErrorMessage = computed(() => {
  // 优先使用直接的 error_message 字段
  if (props.message.error_message) {
    return props.message.error_message
  }

  // 其次从 extra_metadata 中获取具体的错误信息
  if (props.message.extra_metadata?.error_message) {
    return props.message.extra_metadata.error_message
  }

  // 对于已知的错误类型，返回默认提示
  switch (props.message.error_type) {
    case 'interrupted':
      return '回答生成已中断'
    case 'content_guard_blocked':
      return '检测到敏感内容，已中断输出'
    case 'unexpect':
      return '生成过程中出现异常'
    case 'agent_error':
      return '智能体获取失败'
    default:
      return null
  }
})

// 引入智能体 store
const agentStore = useAgentStore()
const { availableKnowledgeBases } = storeToRefs(agentStore)
const infoStore = useInfoStore()
// 提取消息来源
const messageSources = computed(() => {
  if (props.message.type === 'ai') {
    return MessageProcessor.extractSourcesFromMessage(props.message, availableKnowledgeBases.value)
  }
  return { knowledgeChunks: [], webSources: [] }
})

// 过滤有效的工具调用
const validToolCalls = computed(() => {
  if (!props.message.tool_calls || !Array.isArray(props.message.tool_calls)) {
    return []
  }

  return props.message.tool_calls.filter((toolCall) => {
    // 过滤掉无效的工具调用
    return (
      toolCall &&
      (toolCall.id || toolCall.name || toolCall.function?.name) &&
      (toolCall.args !== undefined ||
        toolCall.function?.arguments !== undefined ||
        toolCall.tool_call_result !== undefined)
    )
  })
})

const parsedData = computed(() => {
  // Start with default values from the prop to avoid mutation.
  let content = props.message.content.trim() || ''
  let reasoning_content = props.message.additional_kwargs?.reasoning_content || ''

  if (reasoning_content) {
    return {
      content,
      reasoning_content
    }
  }

  // Regex to find <think>...</think> or an unclosed <think>... at the end of the string.
  const thinkRegex = /<think>(.*?)<\/think>|<think>(.*?)$/s
  const thinkMatch = content.match(thinkRegex)

  if (thinkMatch) {
    // The captured reasoning is in either group 1 (closed tag) or 2 (unclosed tag).
    reasoning_content = (thinkMatch[1] || thinkMatch[2] || '').trim()
    // Remove the entire matched <think> block from the original content.
    content = content.replace(thinkMatch[0], '').trim()
  }

  return {
    content,
    reasoning_content
  }
})
</script>

<style lang="less" scoped>
.message-box {
  display: inline-block;
  border-radius: 1.5rem;
  margin: 0.8rem 0;
  padding: 0.625rem 1.25rem;
  user-select: text;
  word-break: break-word;
  word-wrap: break-word;
  font-size: 15px;
  line-height: 24px;
  box-sizing: border-box;
  color: var(--gray-10000);
  max-width: 100%;
  position: relative;
  letter-spacing: 0.25px;

  &.human,
  &.sent {
    max-width: 95%;
    color: var(--gray-1000);
    background-color: color-mix(in srgb, var(--main-50) 85%, transparent);
    align-self: flex-end;
    border-radius: 0.5rem;
    padding: 0.5rem 1rem;

    // 原地吸顶悬浮
    position: sticky;
    top: 12px;
    z-index: 10;

    // 磨砂玻璃半透效果
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);

    // 投影增强，用于在悬浮或重合时提高界限识别度
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);

    // 适配暗色模式阴影
    .dark &,
    [data-theme='dark'] & {
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3);
    }
  }

  &.is-shrunk {
    padding: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
    border: none !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
  }

  &.assistant,
  &.received,
  &.ai {
    color: initial;
    width: 100%;
    text-align: left;
    margin: 0;
    padding: 0px;
    background-color: transparent;
    border-radius: 0;
  }

  .message-text {
    max-width: 100%;
    margin-bottom: 0;
    white-space: pre-wrap;

    &.render-html {
      word-break: break-all;
    }
  }

  .message-copy-btn {
    cursor: pointer;
    color: var(--gray-400);
    transition: all 0.2s ease;
  }

  // 历史消息气泡提及药丸精致流式样式 (引入共享 Less 模块，开启暗色自适应与只读展示)
  @import '@/assets/css/mention-pill.less';

  .human-message-wrapper {
    position: relative;
    width: 100%;
    display: flex;
    flex-direction: column;
    padding-bottom: 0px; // 保持气泡完美包裹文字，不加底部内边距

    .message-text {
      flex: 1;
      min-width: 0;
      transition: max-height 0.3s ease;
    }

    // 绝对定位在气泡外部右下角的动作条 (彻底移出淡蓝色框外)
    .human-action-bar {
      position: absolute;
      right: 0px;
      bottom: -20px; // 使用负数 bottom，使其完全悬浮在淡蓝色气泡的下边缘外面！
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
      pointer-events: auto;
      z-index: 12;

      // 内联复制按钮样式 (正圆形小按钮，鼠标不放上去不要显示)
      .inline-copy {
        opacity: 0;
        pointer-events: none;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        color: var(--gray-700);
        background: var(--main-0);
        border: 1px solid var(--gray-200);
        box-shadow: 0 3px 8px rgba(0, 0, 0, 0.08);
        cursor: pointer;

        &:hover {
          background: var(--main-50);
          color: var(--main-700);
          border-color: var(--main-200);
          transform: translateY(-1px);
          box-shadow: 0 4px 10px rgba(0, 0, 0, 0.12);
        }

        &.is-copied {
          opacity: 1 !important;
          pointer-events: auto !important;
          color: var(--color-success-500);
          background: var(--main-0);
          border-color: var(--color-success-500);
        }
      }
    }

    // 悬浮在气泡上时高亮显示复制按钮
    &:hover {
      .human-action-bar .inline-copy {
        opacity: 0.85;
        pointer-events: auto;
      }
    }

    // 向右收缩后的悬浮胶囊/球样式
    .stuck-shrunk-ball {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 6px 12px;
      border-radius: 18px;
      background: var(--main-0);
      border: 1px solid var(--gray-200);
      box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08);
      color: var(--main-700);
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s ease;
      animation: fadeInUp 0.2s ease;

      &:hover {
        background: var(--main-50);
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
        
        .arrow-hint {
          transform: translateX(-2px);
        }
      }

      .arrow-hint {
        transition: transform 0.2s ease;
        color: var(--gray-400);
      }
    }

    // 吸顶多行状态下的特定布局
    &.is-stuck-layout {
      gap: 8px;

      // 如果处于折叠状态，左右排列，文字限制单行显示
      &.is-collapsed {
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
        gap: 12px;

        .message-text {
          max-height: 24px;
          white-space: nowrap;
          text-overflow: ellipsis;
          overflow: hidden;
          padding-right: 70px; // 给右侧绝对定位的动作条留出呼吸宽度，彻底防止文字重叠
        }

        .human-action-bar {
          top: 50%;
          bottom: auto;
          transform: translateY(-50%);
          right: 0px;
        }
      }

      // 如果处于展开状态，动作按钮挂在下边缘外侧
      &:not(.is-collapsed) {
        .human-action-bar {
          right: 0px;
          bottom: -20px;
        }
      }
    }
  }

  // 动作条通用图标按钮样式 (正圆形小球悬浮按钮)
  .action-icon-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: var(--main-0);
    border: 1px solid var(--gray-200);
    box-shadow: 0 3px 8px rgba(0, 0, 0, 0.08);
    color: var(--gray-700);
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);

    &:hover {
      background: var(--main-50);
      color: var(--main-700);
      border-color: var(--main-200);
      transform: translateY(-1px);
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.12);
    }
  }

  .message-text-system {
    max-width: 100%;
    margin-bottom: 0;
    white-space: pre-line;
    color: var(--gray-600);
    font-style: italic;
    font-size: 14px;
    padding: 8px 12px;
    background-color: var(--gray-50);
    border-left: 3px solid var(--gray-300);
    border-radius: 4px;
  }

  .err-msg {
    color: var(--color-error-500);
    border: 1px solid currentColor;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    text-align: left;
    background: var(--color-error-50);
    margin-bottom: 10px;
    cursor: pointer;
  }

  .searching-msg {
    color: var(--gray-700);
    animation: colorPulse 1s infinite ease-in-out;
  }

  .reasoning-box {
    margin-top: 10px;
    margin-bottom: 15px;
    border-radius: 8px;
    border: 1px solid var(--gray-150);
    background-color: var(--gray-25);
    overflow: hidden;
    transition: all 0.2s ease;

    :deep(.ant-collapse) {
      background-color: transparent;
      border: none;

      .ant-collapse-item {
        border: none;

        .ant-collapse-header {
          padding: 8px 12px;
          font-size: 14px;
          font-weight: 500;
          color: var(--gray-700);
          transition: all 0.2s ease;

          .ant-collapse-expand-icon {
            color: var(--gray-400);
          }
        }

        .ant-collapse-content {
          border: none;
          background-color: transparent;

          .ant-collapse-content-box {
            padding: 16px;
            background-color: var(--gray-25);
          }
        }
      }
    }

    .reasoning-content {
      font-size: 13px;
      color: var(--gray-800);
      white-space: pre-wrap;
      margin: 0;
      line-height: 1.6;
    }
  }

  .assistant-message {
    width: 100%;
  }

  .error-hint {
    margin: 10px 0;
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 14px;
    display: flex;
    align-items: center;
    gap: 8px;
    background-color: var(--color-error-50);
    color: var(--color-error-500);
    span {
      line-height: 1.5;
    }
  }

  .status-info {
    display: block;
    background-color: var(--gray-50);
    color: var(--gray-700);
    padding: 10px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 12px;
    font-family: monospace;
    max-height: 200px;
    overflow-y: auto;
  }
}

.retry-hint {
  margin-top: 8px;
  padding: 8px 16px;
  color: var(--gray-600);
  font-size: 14px;
  text-align: left;
}

.retry-link {
  color: var(--color-info-500);
  cursor: pointer;
  margin-left: 4px;

  &:hover {
    text-decoration: underline;
  }
}

.ant-btn-icon-only {
  &:has(.anticon-stop) {
    background-color: var(--color-error-500) !important;

    &:hover {
      background-color: var(--color-error-100) !important;
    }
  }
}

@keyframes colorPulse {
  0% {
    color: var(--gray-700);
  }
  50% {
    color: var(--gray-300);
  }
  100% {
    color: var(--gray-700);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

// 多模态消息样式
.message-image {
  border-radius: 12px;
  overflow: hidden;
  margin-left: auto;
  /* max-height: 200px; */
  border: 1px solid rgba(255, 255, 255, 0.2);

  img {
    max-width: 100%;
    max-height: 200px;
    object-fit: contain;
  }
}

.message-md {
  margin: 8px 0;
}
</style>
