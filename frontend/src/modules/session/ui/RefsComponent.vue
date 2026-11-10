<template>
  <div class="refs" v-if="showRefs">
    <div class="tags">
      <button
        v-if="feedbackEntry"
        type="button"
        class="item btn feedback-vote"
        :class="{ selected: feedbackEntry.record?.rating === 'like' }"
        :aria-pressed="feedbackEntry.record?.rating === 'like'"
        :disabled="feedbackEntry.pending || !feedbackEntry.loaded"
        @click="rate('like', $event)"
        :title="feedbackEntry.record?.rating === 'like' ? '已点赞，再次点击撤销' : '点赞：反馈供平台管理员用于改进 Agent'"
        aria-label="点赞"
      >
        <ThumbsUp :size="12" aria-hidden="true" />
      </button>
      <button
        v-if="feedbackEntry"
        type="button"
        class="item btn feedback-vote"
        :class="{ selected: feedbackEntry.record?.rating === 'dislike' }"
        :aria-pressed="feedbackEntry.record?.rating === 'dislike'"
        :disabled="feedbackEntry.pending || !feedbackEntry.loaded"
        @click="rate('dislike', $event)"
        :title="feedbackEntry.record?.rating === 'dislike' ? '已点踩，再次点击撤销' : '点踩：反馈供平台管理员用于改进 Agent'"
        aria-label="点踩"
      >
        <ThumbsDown :size="12" aria-hidden="true" />
      </button>
      <span v-if="feedbackEntry?.pending" class="feedback-status" role="status">
        保存中…
      </span>
      <!-- 模型名称 -->
      <span v-if="showKey('model') && displayModelName" class="item">
        <Bot size="12" /> {{ displayModelName }}
      </span>
      <!-- 复制 -->
      <span v-if="showKey('copy')" class="item btn" @click="copyText(msg.content)" title="复制">
        <Check v-if="isCopied" size="12" />
        <Copy v-else size="12" />
      </span>

      <!-- 对话结束时间 / 执行耗时：纯文本，紧挨复制按钮展示 -->
      <span
        v-if="messageFinishedAt"
        class="time-entry"
        :class="{ toggleable: hasMessageDuration }"
        @click="toggleTimeDisplay"
        :title="
          hasMessageDuration
            ? showingDuration
              ? '点击显示结束时间'
              : '点击显示执行耗时'
            : '结束时间'
        "
      >
        <span v-if="showingDuration && messageDurationLabel">{{ messageDurationLabel }}</span>
        <span v-else>{{ messageFinishedAt }}</span>
      </span>

      <!-- 重试 -->
      <span
        v-if="showKey('regenerate')"
        class="item btn"
        @click="regenerateMessage()"
        title="重新生成"
        ><RotateCcw size="12" />
      </span>

      <div
        v-if="(showKey('sources') && (referenceEntry?.available || hasSources)) || (referenceEntry?.error && !referenceEntry.loaded)"
        class="source-actions"
      >
        <button
          v-if="referenceEntry?.available && showKey('sources')"
          type="button"
          class="item btn reference-action"
          :disabled="referenceEntry.annotating"
          :aria-pressed="referenceEntry.references ? referenceEntry.visible : undefined"
          @click="annotateSources"
        >
          <LoaderCircle v-if="referenceEntry.annotating" size="12" class="reference-loading" />
          <Quote v-else size="12" />
          {{ referenceButtonLabel }}
        </button>
        <button
          v-else-if="referenceEntry?.error && !referenceEntry.loaded"
          type="button"
          class="item btn"
          @click="referenceStore.load(threadId, msg.turn_id)"
        >来源加载失败，重试</button>
        <button
          v-if="hasSources && showKey('sources')"
          type="button"
          class="item btn sources-btn"
          :title="'在侧边栏查看来源详情'"
          @click="openSourcesPanel"
        >
          <BookOpen size="12" />
          <span class="sources-label">
            来源
            <template v-if="sourceCount > 0">
              {{ sourceCount }}
            </template>
          </span>
        </button>
      </div>
    </div>

    <p v-if="feedbackEntry?.error && !feedbackDialogOpen" class="feedback-error" role="alert">
      {{ feedbackEntry.error }}
      <button v-if="!feedbackEntry.loaded" type="button" class="item btn" :disabled="feedbackEntry.loading" @click="feedbackStore.load(threadId, [msg.run_id])">重新读取</button>
      <span v-else>请重试刚才的操作。</span>
    </p>
    <UserFeedbackModal
      v-if="feedbackEntry"
      :open="feedbackDialogOpen"
      :entry="feedbackEntry"
      :run-id="msg.run_id"
      @update-draft="feedbackEntry.draft = $event"
      @save="saveFeedback"
      @close="closeFeedback"
      @after-close="feedbackDialogOpen = false; feedbackTrigger?.focus()"
    />

    <p v-if="referenceEntry?.error && referenceEntry.loaded" class="reference-notice" role="alert">
      {{ referenceEntry.error }}
    </p>
    <p v-else-if="referenceEntry?.references && referenceEntry.visible && !referenceEntry.references.citations.length" class="reference-notice" role="status">
      未找到支持回答的引用片段
    </p>
  </div>

</template>

<script setup>
import { ref, computed, onDeactivated, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { message as antMessage } from 'ant-design-vue'
import {
  ThumbsUp,
  ThumbsDown,
  Bot,
  Copy,
  Check,
  RotateCcw,
  BookOpen,
  Quote,
  LoaderCircle
} from '@lucide/vue'
import { formatChatTime } from '@/shared/lib/time'
import UserFeedbackModal from '@/modules/session/ui/UserFeedbackModal.vue'
import { formatRunTimingDuration, getRunTotalLatencyMs } from '@/modules/session/model/runTiming'
import { useTurnReferencesStore } from '@/modules/session/model/turnReferences'
import { useSessionFeedbackStore } from '@/modules/session/model/sessionFeedback'
import { useModelDirectory } from '@/modules/settings'

const emit = defineEmits(['retry', 'openRefs'])
const props = defineProps({
  threadId: { type: String, default: '' },
  message: Object,
  run: { type: Object, default: null },
  showRefs: {
    type: [Array, Boolean],
    default: () => false
  },
  isLatestMessage: {
    type: Boolean,
    default: false
  },
  sources: {
    type: Object,
    default: () => ({})
  }
})

const msg = ref(props.message)
const referenceStore = useTurnReferencesStore()
const referenceEntry = computed(() =>
  props.threadId && msg.value?.turn_id && msg.value?.phase === 'final_answer'
    ? referenceStore.getEntry(props.threadId, msg.value.turn_id)
    : null
)
const referenceButtonLabel = computed(() => {
  if (referenceEntry.value?.annotating) return '正在标注…'
  if (referenceEntry.value?.references) return referenceEntry.value.visible ? '隐藏标注' : '显示标注'
  return '标注来源'
})
const annotateSources = () => {
  if (referenceEntry.value?.references) {
    referenceEntry.value.visible = !referenceEntry.value.visible
    return
  }
  return referenceStore.annotate(props.threadId, msg.value.turn_id)
}
watch(
  [() => props.threadId, () => props.message?.turn_id, () => props.message?.phase],
  ([threadId, turnId, phase]) => {
    if (threadId && turnId && phase === 'final_answer') void referenceStore.load(threadId, turnId)
  },
  { immediate: true }
)

// Sources state
const knowledgeChunks = computed(() => {
  if (!referenceEntry.value?.loaded) {
    return Array.isArray(props.sources?.knowledgeChunks) ? props.sources.knowledgeChunks : []
  }
  return referenceEntry.value.sources
    .filter((source) => source.kind === 'knowledge')
    .map((source) => ({
      kb_id: source.kb_id,
      file_id: source.file_id,
      content: source.content,
      metadata: {
        source: source.title,
        chunk_id: source.chunk_id,
        start_line: source.start_line,
        end_line: source.end_line
      }
    }))
})
const webSources = computed(() => {
  if (!referenceEntry.value?.loaded) {
    return Array.isArray(props.sources?.webSources) ? props.sources.webSources : []
  }
  return referenceEntry.value.sources.filter((source) => source.kind === 'web')
})

const hasSources = computed(() => knowledgeChunks.value.length > 0 || webSources.value.length > 0)

const sourceCount = computed(() => knowledgeChunks.value.length + webSources.value.length)

/** 来源详情统一在右侧栏展示，附带当前回退数据供标注接口缺失时兜底。 */
const openSourcesPanel = () => {
  emit('openRefs', {
    turnId: msg.value?.turn_id || '',
    knowledgeChunks: knowledgeChunks.value,
    webSources: webSources.value
  })
}

// 对话结束时间 / 执行耗时切换
const showingDuration = ref(false)
const messageFinishedAt = computed(() => {
  const finishedAt = props.run?.timing?.finished_at || msg.value?.created_at
  return finishedAt ? formatChatTime(finishedAt) : ''
})
const messageDurationMs = computed(() => {
  return getRunTotalLatencyMs(props.run?.timing)
})
const hasMessageDuration = computed(() => messageDurationMs.value !== null)
const messageDurationLabel = computed(() => {
  const duration = formatRunTimingDuration(messageDurationMs.value)
  return duration ? `耗时 ${duration}` : ''
})
const toggleTimeDisplay = () => {
  if (!hasMessageDuration.value) return
  showingDuration.value = !showingDuration.value
}

// 监听 message prop 变化 (用于切换对话时更新状态)
watch(
  () => props.message,
  () => {
    msg.value = props.message
    showingDuration.value = false
  },
  { immediate: true }
)

// 使用 useClipboard 实现复制功能
const { copy, isSupported } = useClipboard()

const showKey = (key) => {
  if (props.showRefs === true) {
    return true
  }
  return props.showRefs.includes(key)
}

// 复制状态
const isCopied = ref(false)

// 定义 copy 方法
const copyText = async (text) => {
  if (isSupported) {
    try {
      await copy(text)
      antMessage.success('文本已复制到剪贴板')
      isCopied.value = true
      setTimeout(() => {
        isCopied.value = false
      }, 2000)
    } catch (error) {
      console.error('复制失败:', error)
      antMessage.error('复制失败，请手动复制')
    }
  } else {
    console.warn('浏览器不支持自动复制')
    antMessage.warning('浏览器不支持自动复制，请手动复制')
  }
}

const showRefs = computed(() => {
  // 如果只是为了显示模型信息，不需要检查状态
  if (props.showRefs && Array.isArray(props.showRefs) && props.showRefs.includes('model')) {
    return true
  }
  // 原有的逻辑
  return (
    (msg.value.role == 'received' || msg.value.role == 'assistant') &&
    msg.value.status == 'finished'
  )
})

// 添加重新生成方法
const regenerateMessage = () => {
  emit('retry')
}

// 获取模型名称
const getModelName = (msg) => {
  if (msg.response_metadata?.model_name) {
    return msg.response_metadata.model_name
  }
  return null
}

// 模型名称：优先展示后端配置的别名，目录未就绪或未命中时回退到响应原始名称。
const modelDirectory = useModelDirectory('chat')
const resolvedModelName = ref('')
const rawModelName = computed(() => msg.value?.response_metadata?.model_name || '')
const displayModelName = computed(() => resolvedModelName.value || getModelName(msg.value) || '')
watch(
  rawModelName,
  async (raw) => {
    resolvedModelName.value = ''
    if (!raw) return
    try {
      await modelDirectory.load()
      // 目录加载期间本条消息的原始模型名已变化时放弃本次结果，由 watch 重新触发。
      if (rawModelName.value !== raw) return
      resolvedModelName.value = modelDirectory.resolveDisplayName({ modelId: raw })
    } catch {
      resolvedModelName.value = ''
    }
  },
  { immediate: true }
)
const feedbackStore = useSessionFeedbackStore()
// phase 来自服务端 result_run_id 与 output_message_id 的持久关联，流式 done 不足以开放评价。
const feedbackEntry = computed(() => props.threadId && msg.value?.run_id && msg.value?.turn_id &&
  msg.value.phase === 'final_answer' && msg.value.execution_status === 'completed' &&
  typeof msg.value.content === 'string' && msg.value.content.trim()
  ? feedbackStore.getEntry(props.threadId, msg.value.run_id) : null)
// 同一 Run 的评价共享，弹窗只由当前触发它的视图打开。
const feedbackDialogOpen = ref(false)
let feedbackTrigger = null
let feedbackViewRevision = 0
/** 关闭当前视图的弹窗，保留已提交评价与未提交草稿。 */
function closeFeedback() {
  feedbackDialogOpen.value = false
}
onDeactivated(() => {
  // 返回缓存视图后，离开前尚未完成的评价也不能重新打开弹窗。
  feedbackViewRevision += 1
  if (feedbackDialogOpen.value) closeFeedback()
})
/** 已保存评价由 Store 持有，当前视图关闭自己的说明弹窗。 */
async function saveFeedback(reason) {
  if (await feedbackStore.saveReason(props.threadId, msg.value.turn_id, msg.value.run_id, reason)) closeFeedback()
}
async function rate(rating, event) {
  const entry = feedbackEntry.value
  const removing = entry.record?.rating === rating
  const revision = feedbackViewRevision
  const trigger = event.currentTarget
  feedbackTrigger = trigger
  await feedbackStore.rate(props.threadId, msg.value.turn_id, msg.value.run_id, rating)
  if (revision === feedbackViewRevision && !removing && !entry.error &&
      entry.record?.rating === rating && trigger.isConnected) {
    feedbackDialogOpen.value = true
  }
}
</script>

<style lang="less" scoped>
.reference-action {
  border: 1px solid transparent;
  background: transparent;
  font: inherit;
  &:disabled { cursor: wait; opacity: 0.6; }
  &:focus-visible { outline: 2px solid var(--main-500); outline-offset: 2px; }
}
.reference-notice {
  margin: 6px 0;
  color: var(--gray-600);
  font-size: 12px;
}
.reference-loading { animation: reference-spin 1s linear infinite; }
@keyframes reference-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .reference-loading { animation: none; } }
.refs {
  display: flex;
  flex-direction: column;
  margin-bottom: 20px;
  margin-top: 10px;
  color: var(--gray-500);
  font-size: 13px;
  gap: 12px;

  .item {
    background: var(--gray-50);
    color: var(--gray-700);
    padding: 6px 8px;
    border-radius: 8px;
    font-size: 13px;
    user-select: none;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    line-height: 1;

    &.btn {
      cursor: pointer;
      &:hover {
        background: var(--gray-100);
      }
      &:active {
        background: var(--gray-200);
      }
    }
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    width: 100%;

    .source-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-left: auto;
    }

    .sources-btn {
      background: var(--gray-50);
      border: 1px solid transparent;
      padding: 6px 10px;

      &:hover {
        background: var(--gray-100);
      }

      &:focus-visible {
        outline: 2px solid var(--main-500);
        outline-offset: 2px;
      }

      .sources-label {
        font-weight: 500;
        margin-left: 2px;
      }
    }

    .time-entry {
      color: var(--gray-400);
      font-variant-numeric: tabular-nums;
      user-select: none;
      // 默认隐藏，悬浮 refs 行或所在 Run 分组时才显示
      opacity: 0;
      visibility: hidden;
      transition:
        opacity 0.2s ease,
        visibility 0.2s ease;

      &.toggleable {
        cursor: pointer;

        &:hover {
          color: var(--gray-700);
        }
      }
    }
  }

  // 悬浮 refs 行时显示时间
  &:hover .time-entry {
    opacity: 1;
    visibility: visible;
  }
}

.refs .feedback-vote {
  border: 0;
  font-family: inherit;
  &.selected { color: var(--main-color); }
  &:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
  &:disabled { opacity: 0.5; cursor: wait; }
}
.feedback-error { color: var(--color-error-500); margin: 0; }
.feedback-status { font-size: 12px; }
</style>
