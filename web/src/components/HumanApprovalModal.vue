<template>
  <transition name="slide-up">
    <div
      v-if="visible"
      class="approval-modal"
      :class="{ 'is-tool-approval': isToolApproval, 'is-question-dialog': !isToolApproval }"
      role="dialog"
      :aria-labelledby="isToolApproval ? 'tool-approval-question' : 'question-dialog-title'"
      :aria-describedby="isToolApproval ? 'tool-approval-summary' : undefined"
    >
      <div class="approval-content">
        <div v-if="isToolApproval" class="tool-approval-block">
          <div class="approval-header tool-approval-header">
            <div class="tool-approval-context">
              <span class="tool-approval-icon">
                <component :is="activeToolIcon" :size="17" />
              </span>
              <span>{{ toolDisplayName(activeToolRequest?.name) }}</span>
              <code>{{ activeToolRequest?.name }}</code>
            </div>
            <span v-if="actionRequests.length > 1" class="tool-progress-label">
              {{ activeToolIndex + 1 }} / {{ actionRequests.length }}
            </span>
          </div>

          <div
            v-if="actionRequests.length > 1"
            class="tool-approval-progress"
            aria-label="审批进度"
          >
            <span
              v-for="(_, index) in actionRequests"
              :key="index"
              class="tool-progress-step"
              :class="{
                active: index === activeToolIndex,
                completed: Boolean(toolDecisions[index])
              }"
            >
              {{ index + 1 }}
            </span>
          </div>

          <h4 id="tool-approval-question" class="tool-approval-question">
            {{ toolApprovalQuestion }}
          </h4>

          <div
            v-if="activeToolRequest"
            class="tool-args-disclosure"
            :class="{ 'is-expanded': toolArgsExpanded }"
          >
            <button
              id="tool-approval-summary"
              type="button"
              class="tool-args-trigger"
              :aria-expanded="toolArgsExpanded"
              aria-controls="tool-approval-full-args"
              @click="toolArgsExpanded = !toolArgsExpanded"
            >
              <code>{{ toolApprovalSummary }}</code>
              <ChevronDown
                :size="15"
                class="tool-args-chevron"
                :class="{ 'is-expanded': toolArgsExpanded }"
              />
            </button>
            <transition name="tool-args-expand">
              <div v-if="toolArgsExpanded" class="tool-args-panel">
                <pre id="tool-approval-full-args" class="tool-args-expanded">{{
                  formattedToolArgs
                }}</pre>
              </div>
            </transition>
          </div>
        </div>

        <div v-else class="question-dialog-header">
          <div class="question-dialog-heading">
            <CircleHelp :size="19" aria-hidden="true" />
            <span>问题</span>
          </div>
          <div class="question-dialog-navigation">
            <button
              type="button"
              class="question-icon-button"
              aria-label="上一题"
              :disabled="isProcessing || activeQuestionIndex === 0"
              @click="setActiveQuestion(activeQuestionIndex - 1)"
            >
              <ChevronLeft :size="19" />
            </button>
            <span class="question-progress" aria-live="polite">
              {{ activeQuestionIndex + 1 }} / {{ normalizedQuestions.length }}
            </span>
            <button
              type="button"
              class="question-icon-button"
              aria-label="下一题"
              :disabled="isProcessing || activeQuestionIndex >= normalizedQuestions.length - 1"
              @click="setActiveQuestion(activeQuestionIndex + 1)"
            >
              <ChevronRight :size="19" />
            </button>
            <button
              type="button"
              class="question-icon-button question-close-button"
              aria-label="关闭问题"
              :disabled="isProcessing"
              @click="handleCancel"
            >
              <X :size="19" />
            </button>
          </div>
        </div>

        <div v-if="!isToolApproval && activeQuestion" class="question-block">
          <h4
            id="question-dialog-title"
            ref="questionTitleRef"
            class="question-title"
            tabindex="-1"
          >
            <span>{{ questionTypeLabel }}：</span>{{ activeQuestion.question }}
          </h4>

          <div v-if="activeQuestion.operation" class="approval-operation">
            <span class="label">操作：</span>
            <span class="operation-text">{{ activeQuestion.operation }}</span>
          </div>

          <div v-if="activeQuestion.answerMode !== 'text'" class="question-options">
            <label
              v-for="(optionItem, optionIndex) in activeQuestion.options"
              :key="`${activeQuestion.questionId}-${optionItem.value}-${optionIndex}`"
              class="option-item"
              :class="{
                selected: getSelected(activeQuestion.questionId).includes(optionItem.value)
              }"
            >
              <input
                v-if="activeQuestion.multiSelect"
                type="checkbox"
                :value="optionItem.value"
                :checked="getSelected(activeQuestion.questionId).includes(optionItem.value)"
                :disabled="isProcessing"
                @change="toggleSelect(activeQuestion.questionId, optionItem.value)"
              />
              <input
                v-else
                type="radio"
                :name="`approval-option-${activeQuestion.questionId}`"
                :value="optionItem.value"
                :checked="getSelected(activeQuestion.questionId)[0] === optionItem.value"
                :disabled="isProcessing"
                @change="setSingle(activeQuestion.questionId, optionItem.value)"
              />
              <span class="option-index" aria-hidden="true">{{ optionIndex + 1 }}</span>
              <div class="option-content">
                <span
                  class="option-label"
                  :class="{
                    recommended:
                      optionIndex === 0 && String(optionItem.label).includes('(Recommended)')
                  }"
                >
                  {{ optionItem.label }}
                </span>
                <span v-if="optionItem.description" class="option-description">
                  {{ optionItem.description }}
                </span>
              </div>
            </label>
          </div>

          <div
            class="question-response-bar"
            :class="{
              'free-text-answer': activeQuestion.answerMode === 'text',
              'other-input': activeQuestion.answerMode !== 'text',
              selected:
                activeQuestion.answerMode !== 'text' && isCustomAnswerSelected(activeQuestion)
            }"
          >
            <template v-if="activeQuestion.answerMode === 'text'">
              <label class="visually-hidden" :for="`question-answer-${activeQuestion.questionId}`">
                回答 {{ activeQuestion.question }}
              </label>
              <textarea
                :id="`question-answer-${activeQuestion.questionId}`"
                ref="answerTextareaRef"
                :value="answerTexts[activeQuestion.questionId] || ''"
                :disabled="isProcessing"
                rows="2"
                placeholder="请输入你的回答…"
                @input="handleTextInput(activeQuestion.questionId, $event)"
              ></textarea>
            </template>
            <template v-else-if="activeQuestion.allowOther">
              <PencilLine :size="17" aria-hidden="true" />
              <label class="visually-hidden" :for="`other-answer-${activeQuestion.questionId}`">
                自行填写回答
              </label>
              <textarea
                :id="`other-answer-${activeQuestion.questionId}`"
                ref="otherTextareaRef"
                :value="answerTexts[activeQuestion.questionId] || ''"
                :disabled="isProcessing"
                rows="1"
                placeholder="或自行填写回答"
                @focus="selectOtherAnswer(activeQuestion.questionId)"
                @input="handleTextInput(activeQuestion.questionId, $event)"
              ></textarea>
            </template>
            <span v-else class="response-bar-spacer" aria-hidden="true"></span>
            <div class="question-inline-actions">
              <button class="btn btn-skip" @click="handleSkip" :disabled="isProcessing">
                跳过
              </button>
              <button
                class="btn btn-approve"
                @click="handlePrimaryAction"
                :disabled="isPrimaryButtonDisabled"
              >
                {{ primaryButtonText }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="isToolApproval" class="approval-actions tool-approval-actions">
        <button
          ref="toolRejectButtonRef"
          type="button"
          class="btn btn-reject"
          :disabled="isProcessing"
          @click="handleToolDecision('reject')"
        >
          拒绝
        </button>
        <button
          type="button"
          class="btn btn-approve"
          :disabled="isProcessing"
          @click="handleToolDecision('approve')"
        >
          允许
        </button>
      </div>

      <div v-if="isProcessing" class="approval-processing">
        <span class="processing-spinner"></span>
        处理中...
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  PencilLine,
  Wrench,
  X
} from '@lucide/vue'
import {
  buildQuestionAnswer as buildQuestionAnswerValue,
  isQuestionAnswered as hasQuestionAnswer,
  normalizeQuestions
} from '@/utils/questionUtils'
import { getToolIcon } from '@/components/ToolCallingResult/toolRegistry'
import {
  buildToolApprovalDecisions,
  formatToolApprovalArgs,
  getToolApprovalSummary
} from '@/utils/toolApproval'

const TOOL_DISPLAY_NAMES = {
  write_file: '写入文件',
  edit_file: '编辑文件',
  execute: '执行命令'
}

const props = defineProps({
  visible: { type: Boolean, default: false },
  questions: { type: Array, default: () => [] },
  kind: { type: String, default: 'question' },
  actionRequests: { type: Array, default: () => [] }
})

const emit = defineEmits(['submit', 'cancel'])

const isProcessing = ref(false)
const activeQuestionIndex = ref(0)
const selectedValues = ref({})
const answerTexts = ref({})
const skippedQuestionIds = ref([])
const customAnswerQuestionIds = ref([])
const otherTextareaRef = ref(null)
const answerTextareaRef = ref(null)
const questionTitleRef = ref(null)
const toolRejectButtonRef = ref(null)
const toolArgsExpanded = ref(false)
const toolDecisions = ref({})
const activeToolIndex = ref(0)
const OTHER_TEXTAREA_MAX_ROWS = 4

const normalizedQuestions = computed(() => {
  return normalizeQuestions(props.questions)
})
const isToolApproval = computed(() => props.kind === 'tool_approval')
const activeToolRequest = computed(() => props.actionRequests[activeToolIndex.value] || null)
const activeToolIcon = computed(() => getToolIcon(activeToolRequest.value?.name) || Wrench)
const toolApprovalQuestion = computed(() => {
  if (activeToolRequest.value?.name === 'execute') return '是否允许执行以下命令？'
  if (activeToolRequest.value?.name === 'write_file') return '是否允许写入此文件？'
  if (activeToolRequest.value?.name === 'edit_file') return '是否允许编辑此文件？'
  return '是否允许执行此工具操作？'
})
const toolApprovalSummary = computed(() => getToolApprovalSummary(activeToolRequest.value))

const activeQuestion = computed(() => {
  if (normalizedQuestions.value.length === 0) return null
  const index = Math.min(activeQuestionIndex.value, normalizedQuestions.value.length - 1)
  return normalizedQuestions.value[index]
})
const questionTypeLabel = computed(() => {
  if (activeQuestion.value?.answerMode === 'text') return '问答题'
  return activeQuestion.value?.multiSelect ? '多选题' : '单选题'
})

const resetForm = () => {
  isProcessing.value = false
  activeQuestionIndex.value = 0
  selectedValues.value = {}
  answerTexts.value = {}
  skippedQuestionIds.value = []
  customAnswerQuestionIds.value = []
  toolArgsExpanded.value = false
  toolDecisions.value = {}
  activeToolIndex.value = 0
}

const adjustOtherTextareaHeight = () => {
  const textarea =
    activeQuestion.value?.answerMode === 'text' ? answerTextareaRef.value : otherTextareaRef.value
  if (!textarea) return

  const style = window.getComputedStyle(textarea)
  const lineHeight = Number.parseFloat(style.lineHeight) || 20
  const paddingY =
    (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0)
  const borderY =
    (Number.parseFloat(style.borderTopWidth) || 0) +
    (Number.parseFloat(style.borderBottomWidth) || 0)
  const maxHeight = lineHeight * OTHER_TEXTAREA_MAX_ROWS + paddingY + borderY

  textarea.style.height = 'auto'
  textarea.style.maxHeight = `${maxHeight}px`
  textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

const focusActiveQuestion = () => {
  if (activeQuestion.value?.answerMode === 'text') {
    answerTextareaRef.value?.focus()
    return
  }
  questionTitleRef.value?.focus()
}

const markQuestionAnswered = (questionId) => {
  skippedQuestionIds.value = skippedQuestionIds.value.filter((id) => id !== questionId)
}

const handleTextInput = (questionId, event) => {
  answerTexts.value[questionId] = event.target.value
  if (activeQuestion.value?.answerMode !== 'text') {
    selectOtherAnswer(questionId)
  }
  markQuestionAnswered(questionId)
  adjustOtherTextareaHeight()
}

const selectOtherAnswer = (questionId) => {
  const question = normalizedQuestions.value.find((item) => item.questionId === questionId)
  if (!question || question.answerMode === 'text' || !question.allowOther) return

  if (!question.multiSelect) selectedValues.value[questionId] = []
  if (!customAnswerQuestionIds.value.includes(questionId)) {
    customAnswerQuestionIds.value = [...customAnswerQuestionIds.value, questionId]
  }
  markQuestionAnswered(questionId)
}

const setActiveQuestion = (index) => {
  if (isProcessing.value) return
  if (index < 0 || index >= normalizedQuestions.value.length) return
  activeQuestionIndex.value = index
  nextTick(() => {
    adjustOtherTextareaHeight()
    focusActiveQuestion()
  })
}

const syncAnswersWithQuestions = () => {
  const nextSelectedValues = {}
  const nextOtherTexts = {}
  const validQuestionIds = new Set(normalizedQuestions.value.map((question) => question.questionId))
  const eligibleCustomAnswerIds = new Set(
    normalizedQuestions.value
      .filter((question) => question.answerMode !== 'text' && question.allowOther)
      .map((question) => question.questionId)
  )

  normalizedQuestions.value.forEach((questionItem) => {
    const questionId = questionItem.questionId

    const previousSelected = Array.isArray(selectedValues.value[questionId])
      ? selectedValues.value[questionId]
      : []
    const validSelected = previousSelected.filter((value) =>
      questionItem.options.some((option) => option?.value === value)
    )

    if (questionItem.multiSelect) {
      nextSelectedValues[questionId] = validSelected
    } else {
      const current = validSelected[0]
      if (current) {
        nextSelectedValues[questionId] = [current]
      } else {
        nextSelectedValues[questionId] = []
      }
    }

    const text = String(answerTexts.value[questionId] || '').trim()
    if (text) {
      nextOtherTexts[questionId] = text
    }
  })

  selectedValues.value = nextSelectedValues
  answerTexts.value = nextOtherTexts
  skippedQuestionIds.value = skippedQuestionIds.value.filter((id) => validQuestionIds.has(id))
  customAnswerQuestionIds.value = customAnswerQuestionIds.value.filter((id) =>
    eligibleCustomAnswerIds.has(id)
  )
}

const getSelected = (questionId) => {
  const selected = selectedValues.value[questionId]
  return Array.isArray(selected) ? selected : []
}

const isCustomAnswerSelected = (questionItem) =>
  customAnswerQuestionIds.value.includes(questionItem.questionId)

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      activeQuestionIndex.value = 0
      activeToolIndex.value = 0
      toolArgsExpanded.value = false
      nextTick(() => {
        adjustOtherTextareaHeight()
        if (isToolApproval.value) {
          toolRejectButtonRef.value?.focus()
        } else {
          focusActiveQuestion()
        }
      })
      return
    }

    if (!newVal) {
      resetForm()
    }
  },
  { immediate: true }
)

watch(
  normalizedQuestions,
  () => {
    syncAnswersWithQuestions()
    if (activeQuestionIndex.value >= normalizedQuestions.value.length) {
      activeQuestionIndex.value = Math.max(0, normalizedQuestions.value.length - 1)
    }
    nextTick(() => {
      adjustOtherTextareaHeight()
    })
  },
  { immediate: true, deep: true }
)

const toggleSelect = (questionId, value) => {
  if (isProcessing.value) return

  customAnswerQuestionIds.value = customAnswerQuestionIds.value.filter((id) => id !== questionId)
  const current = getSelected(questionId)
  if (current.includes(value)) {
    selectedValues.value[questionId] = current.filter((item) => item !== value)
  } else {
    selectedValues.value[questionId] = [...current, value]
  }
  markQuestionAnswered(questionId)
  nextTick(() => {
    adjustOtherTextareaHeight()
  })
}

const setSingle = (questionId, value) => {
  if (isProcessing.value) return
  selectedValues.value[questionId] = [value]
  customAnswerQuestionIds.value = customAnswerQuestionIds.value.filter((id) => id !== questionId)
  markQuestionAnswered(questionId)
  nextTick(() => {
    adjustOtherTextareaHeight()
  })
}

const isQuestionAnswered = (questionItem) => {
  return hasQuestionAnswer(
    questionItem,
    getSelected(questionItem.questionId),
    answerTexts.value[questionItem.questionId],
    isCustomAnswerSelected(questionItem)
  )
}

const isQuestionSkipped = (questionItem) =>
  skippedQuestionIds.value.includes(questionItem.questionId)

const isQuestionComplete = (questionItem) =>
  isQuestionAnswered(questionItem) || isQuestionSkipped(questionItem)

const isSubmitDisabled = computed(() => {
  if (isProcessing.value) return true
  if (normalizedQuestions.value.length === 0) return true

  return normalizedQuestions.value.some((questionItem) => !isQuestionComplete(questionItem))
})

const isLastQuestion = computed(() => {
  if (normalizedQuestions.value.length === 0) return true
  return activeQuestionIndex.value >= normalizedQuestions.value.length - 1
})

const isCurrentQuestionAnswered = computed(() => {
  if (!activeQuestion.value) return false
  return isQuestionAnswered(activeQuestion.value)
})

const primaryButtonText = computed(() => (isLastQuestion.value ? '提交' : '下一步'))

const isPrimaryButtonDisabled = computed(() => {
  if (isProcessing.value) return true
  if (!activeQuestion.value) return true

  if (isLastQuestion.value) {
    return isSubmitDisabled.value
  }

  return !isCurrentQuestionAnswered.value
})

const getQuestionAnswer = (questionItem) => {
  return buildQuestionAnswerValue(
    questionItem,
    getSelected(questionItem.questionId),
    answerTexts.value[questionItem.questionId],
    isCustomAnswerSelected(questionItem)
  )
}

const buildAnswer = () => {
  const answer = {}
  normalizedQuestions.value.forEach((questionItem) => {
    if (isQuestionAnswered(questionItem) && !isQuestionSkipped(questionItem)) {
      answer[questionItem.questionId] = getQuestionAnswer(questionItem)
    }
  })
  return answer
}

const handleSubmit = () => {
  if (isSubmitDisabled.value) return
  isProcessing.value = true
  emit('submit', buildAnswer())
}

const handlePrimaryAction = () => {
  if (isPrimaryButtonDisabled.value) return

  if (isLastQuestion.value) {
    handleSubmit()
    return
  }

  setActiveQuestion(activeQuestionIndex.value + 1)
}

const handleCancel = () => {
  if (isProcessing.value) return
  emit('cancel')
}

const handleSkip = () => {
  if (isProcessing.value || !activeQuestion.value) return

  const questionId = activeQuestion.value.questionId
  if (!skippedQuestionIds.value.includes(questionId)) {
    skippedQuestionIds.value = [...skippedQuestionIds.value, questionId]
  }

  if (isLastQuestion.value) {
    handleSubmit()
    return
  }

  setActiveQuestion(activeQuestionIndex.value + 1)
}

const handleToolDecision = (decision) => {
  if (isProcessing.value || !activeToolRequest.value) return

  const nextDecisions = { ...toolDecisions.value, [activeToolIndex.value]: decision }
  toolDecisions.value = nextDecisions

  if (activeToolIndex.value < props.actionRequests.length - 1) {
    toolArgsExpanded.value = false
    activeToolIndex.value += 1
    return
  }

  isProcessing.value = true
  emit('submit', {
    decisions: buildToolApprovalDecisions(nextDecisions, props.actionRequests.length)
  })
}

const toolDisplayName = (name) => TOOL_DISPLAY_NAMES[name] || '工具调用'

const formattedToolArgs = computed(() => formatToolApprovalArgs(activeToolRequest.value?.args))
</script>

<style scoped lang="less">
.approval-modal {
  background: var(--gray-0);
  border-radius: 12px 12px;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.12);
  margin: 0 auto 8px;
  max-width: 800px;
  min-width: 360px;
  width: fit-content;
  border: 1px solid var(--gray-200);

  &.is-question-dialog {
    align-self: stretch;
    width: 100%;
    max-width: none;
    min-width: 0;
    margin: 0;
    border-radius: 24px;
    box-shadow: 0 6px 18px var(--shadow-1);
  }

  &.is-tool-approval {
    align-self: stretch;
    display: flex;
    flex-direction: column;
    justify-content: center;
    width: 100%;
    max-width: none;
    min-width: 0;
    margin: 0;
    border-radius: 13px;
    box-shadow:
      0 12px 32px var(--shadow-1),
      0 2px 8px var(--shadow-1);
  }
}

.approval-content {
  padding: 16px 20px;
}

.is-question-dialog .approval-content {
  padding: 13px 16px 12px;
}

.question-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.question-dialog-heading,
.question-dialog-navigation {
  display: flex;
  align-items: center;
}

.question-dialog-heading {
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 500;
}

.question-dialog-navigation {
  gap: 4px;
}

.question-progress {
  min-width: 54px;
  color: var(--color-text-secondary);
  font-size: 13px;
  text-align: center;
}

.question-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;

  &:hover:not(:disabled) {
    background: var(--gray-50);
    color: var(--color-text);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
  }
}

.question-close-button {
  margin-left: 4px;
}

.approval-header {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  margin-bottom: 12px;
}

.approval-header h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  color: var(--gray-800);
  text-align: left;
}

.question-block {
  min-height: 0;
}

.question-title {
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.55;
  overflow-wrap: anywhere;

  span {
    font-weight: 600;
  }
}

.tool-approval-header {
  justify-content: space-between;
  margin-bottom: 10px;
}

.tool-approval-context {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;

  code {
    overflow: hidden;
    color: var(--color-text-tertiary);
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.tool-approval-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
}

.tool-approval-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}

.tool-progress-step {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  border: 1px solid var(--gray-150);
  background: var(--gray-25);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;

  &.active {
    border-color: var(--main-300);
    background: var(--main-50);
    color: var(--main-700);
  }

  &.completed {
    border-color: var(--gray-300);
    background: var(--gray-100);
    color: var(--color-text);
  }
}

.tool-progress-label {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.tool-approval-question {
  margin: 0 0 12px;
  color: var(--color-text);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
}

.tool-args-disclosure {
  width: 100%;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
  overflow: hidden;

  &.is-expanded .tool-args-trigger {
    border-bottom: 1px solid var(--gray-150);
  }
}

.tool-args-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  text-align: left;
  cursor: pointer;

  > span,
  > svg {
    flex-shrink: 0;
  }

  code {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--color-text);
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 13px;
    line-height: 1.55;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }
}

.tool-args-chevron {
  transition: transform 0.2s ease;

  &.is-expanded {
    transform: rotate(180deg);
  }
}

.tool-args-panel {
  max-height: 300px;
  overflow: hidden;
}

.tool-args-expanded {
  box-sizing: border-box;
  max-height: 300px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  background: var(--gray-10);
  color: var(--color-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-args-expand-enter-active,
.tool-args-expand-leave-active {
  transition:
    max-height 0.22s ease,
    opacity 0.18s ease;
}

.tool-args-expand-enter-from,
.tool-args-expand-leave-to {
  max-height: 0;
  opacity: 0;
}

.tool-args-expand-enter-to,
.tool-args-expand-leave-from {
  max-height: 300px;
  opacity: 1;
}

.approval-operation {
  background: var(--gray-50);
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.5;
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}

.approval-operation .label {
  color: var(--gray-600);
  font-weight: 500;
  flex-shrink: 0;
}

.approval-operation .operation-text {
  color: var(--gray-800);
  word-break: break-word;
}

.question-options {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.option-item {
  display: flex;
  align-items: center;
  gap: 12px;
  box-sizing: border-box;
  width: 100%;
  min-height: 40px;
  padding: 4px 8px;
  border: 1px solid transparent;
  border-radius: 7px;
  color: var(--color-text);
  font-size: 14px;
  cursor: pointer;

  input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }

  &:hover {
    background: var(--gray-25);
  }

  &:focus-within {
    border-color: var(--main-color);
    outline: 2px solid var(--main-50);
  }

  &.selected {
    background: var(--main-10);

    .option-index {
      border-color: var(--main-color);
      background: var(--main-color);
      color: var(--gray-0);
    }
  }
}

.option-index {
  display: inline-flex;
  flex: 0 0 30px;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--gray-200);
  border-radius: 999px;
  background: var(--gray-25);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.option-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.option-label {
  line-height: 1.4;
  color: var(--color-text);

  &.recommended {
    color: var(--main-color);
    font-weight: 600;
  }
}

.option-description {
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.45;
  word-break: break-word;
}

.question-response-bar {
  box-sizing: border-box;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 42px;
  margin-top: 4px;
  padding: 4px 5px 4px 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease;

  &:hover {
    background: var(--gray-25);
  }
}

.question-response-bar textarea {
  flex: 1;
  min-width: 0;
  background: transparent;
  color: var(--color-text);
  border: 0;
  padding: 5px 10px;
  font-size: 13px;
  line-height: 1.5;
  font-family: inherit;
  outline: none;
  resize: none;
  overflow-y: hidden;
  box-sizing: border-box;
}

.question-response-bar textarea::placeholder {
  color: var(--color-text-secondary);
  opacity: 1;
}

.question-response-bar textarea:focus {
  outline: none;
}

.question-response-bar:focus-within {
  border-color: var(--main-color);
  background: var(--gray-25);
  box-shadow: 0 0 0 2px var(--main-50);
}

.other-input.selected {
  border-color: var(--main-300);
  background: var(--main-10);
}

.free-text-answer {
  align-items: flex-end;
  min-height: 70px;
  border-color: var(--gray-150);
  background: var(--gray-0);

  textarea {
    min-height: 54px;
    max-height: 112px;
    font-size: 14px;
    overflow-y: auto;
  }
}

.response-bar-spacer {
  flex: 1;
}

.question-inline-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;

  .btn {
    min-width: 64px;
    min-height: 34px;
    padding: 5px 12px;
    border-radius: 6px;
  }
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 20px 14px;
}

.tool-approval-actions {
  justify-content: flex-end;
  padding-top: 4px;

  .btn {
    flex: 0 0 auto;
    min-width: 82px;
  }
}

.btn {
  flex: 0 0 auto;
  min-width: 76px;
  min-height: 34px;
  padding: 7px 16px;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.btn-reject {
  border: 1px solid var(--gray-200);
  background: var(--gray-25);
  color: var(--gray-700);
}

.btn-skip {
  border: 1px solid var(--gray-200);
  background: var(--gray-0);
  color: var(--color-text);
}

.btn-skip:hover:not(:disabled) {
  border-color: var(--gray-300);
  background: var(--gray-25);
}

.btn-reject:hover:not(:disabled) {
  background: var(--gray-200);
}

.btn-approve {
  background: var(--main-color);
  color: var(--gray-0);
}

.btn-approve:hover:not(:disabled) {
  background: var(--main-700);
}

.approval-processing {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px;
  color: var(--gray-600);
  font-size: 13px;
  background: var(--gray-25);
  border-top: 1px solid var(--gray-100);
}

.processing-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--gray-300);
  border-top-color: var(--main-color);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.25s ease;
}

.slide-up-enter-from {
  opacity: 0;
  transform: translateY(20px);
}

.slide-up-leave-to {
  opacity: 0;
  transform: translateY(20px);
}

@media (max-width: 520px) {
  .approval-modal {
    width: calc(100vw - 12px);
    min-width: 0;
  }

  .approval-modal.is-tool-approval {
    width: 100%;
  }

  .approval-content {
    padding: 12px 16px;
  }

  .question-dialog-header {
    margin-bottom: 10px;
  }

  .question-dialog-heading {
    font-size: 13px;
  }

  .question-icon-button {
    width: 40px;
    height: 40px;
  }

  .question-progress {
    min-width: 46px;
    font-size: 12px;
  }

  .question-title,
  .approval-header h4 {
    font-size: 14px;
  }

  .approval-operation {
    font-size: 12px;
    padding: 8px 10px;
  }

  .approval-actions {
    padding: 10px 16px 12px;
    gap: 8px;
  }

  .question-response-bar {
    padding-right: 4px;
  }

  .question-inline-actions {
    gap: 6px;

    .btn {
      min-width: 58px;
      min-height: 40px;
      padding: 6px 10px;
    }
  }

  .tool-approval-context code {
    display: none;
  }

  .btn {
    min-height: 32px;
    padding: 6px 14px;
    font-size: 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .slide-up-enter-active,
  .slide-up-leave-active {
    transition: none;
  }

  .processing-spinner {
    animation-duration: 1.5s;
  }

  .tool-args-chevron,
  .tool-args-expand-enter-active,
  .tool-args-expand-leave-active {
    transition: none;
  }
}
</style>
