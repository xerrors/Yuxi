<template>
  <transition name="slide-up">
    <div v-if="visible" class="approval-modal" :class="{ 'is-tool-approval': isToolApproval }">
      <div class="approval-content">
        <div v-if="isToolApproval" class="tool-approval-block">
          <div v-if="actionRequests.length > 1" class="tool-approval-progress" aria-label="审批进度">
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

          <div class="approval-header tool-approval-header">
            <div class="tool-approval-title">
              <CircleHelp :size="18" />
              <h4>是否批准此工具操作？</h4>
            </div>
            <span v-if="actionRequests.length > 1" class="tool-progress-label">
              {{ activeToolIndex + 1 }} / {{ actionRequests.length }}
            </span>
          </div>

          <div v-if="activeToolRequest" class="tool-request-summary">
            <span class="tool-request-icon">
              <component :is="activeToolIcon" :size="18" />
            </span>
            <div class="tool-request-copy">
              <div class="tool-request-name">
                <strong>{{ toolDisplayName(activeToolRequest.name) }}</strong>
                <code>{{ activeToolRequest.name }}</code>
              </div>
              <a-tooltip placement="topLeft">
                <template #title>
                  <pre class="tool-args-tooltip">{{ formattedToolArgs }}</pre>
                </template>
                <button type="button" class="tool-args-preview" aria-label="悬浮查看完整参数">
                  <span>参数</span>
                  <code>{{ previewedToolArgs }}</code>
                  <Info :size="13" />
                </button>
              </a-tooltip>
            </div>
          </div>
        </div>

        <div v-else-if="normalizedQuestions.length > 1" class="question-tabs">
          <button
            v-for="(questionItem, questionIndex) in normalizedQuestions"
            :key="questionItem.questionId"
            class="tab-item"
            :class="{
              active: questionIndex === activeQuestionIndex,
              completed: isQuestionAnswered(questionItem)
            }"
            :disabled="isProcessing"
            @click="setActiveQuestion(questionIndex)"
          >
            <span class="tab-index">{{ questionIndex + 1 }}</span>
          </button>
        </div>

        <div v-if="!isToolApproval && activeQuestion" class="question-block">
          <div class="approval-header">
            <h4>{{ activeQuestionIndex + 1 }}. {{ activeQuestion.question }}</h4>
          </div>

          <div v-if="activeQuestion.operation" class="approval-operation">
            <span class="label">操作：</span>
            <span class="operation-text">{{ activeQuestion.operation }}</span>
          </div>

          <div class="question-options">
            <label
              v-for="(optionItem, optionIndex) in activeQuestion.options"
              :key="`${activeQuestion.questionId}-${optionItem.value}-${optionIndex}`"
              class="option-item"
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
              <span
                :class="{
                  recommended:
                    optionIndex === 0 && String(optionItem.label).includes('(Recommended)')
                }"
              >
                {{ optionItem.label }}
              </span>
            </label>

            <div v-if="shouldShowOtherInput(activeQuestion)" class="other-input">
              <textarea
                ref="otherTextareaRef"
                :value="otherTexts[activeQuestion.questionId] || ''"
                :disabled="isProcessing"
                rows="1"
                placeholder="其他：请输入自定义内容"
                @input="handleOtherTextInput(activeQuestion.questionId, $event)"
              ></textarea>
            </div>
          </div>
        </div>
      </div>

      <div v-if="isToolApproval" class="approval-actions tool-approval-actions">
        <button
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
          批准
        </button>
      </div>

      <div v-else class="approval-actions">
        <button class="btn btn-reject" @click="handleCancel" :disabled="isProcessing">取消</button>
        <button
          class="btn btn-approve"
          @click="handlePrimaryAction"
          :disabled="isPrimaryButtonDisabled"
        >
          {{ primaryButtonText }}
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
import { CircleHelp, Info, Wrench } from 'lucide-vue-next'
import {
  isOtherOption,
  normalizeQuestions,
  DEFAULT_OTHER_OPTION_VALUE
} from '@/utils/questionUtils'
import { getToolIcon } from '@/components/ToolCallingResult/toolRegistry'
import { buildToolApprovalDecisions } from '@/utils/toolApproval'

const TOOL_DISPLAY_NAMES = {
  write_file: '写入文件',
  edit_file: '编辑文件',
  execute: '执行命令',
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
const otherTexts = ref({})
const otherTextareaRef = ref(null)
const toolDecisions = ref({})
const activeToolIndex = ref(0)
const OTHER_TEXTAREA_MAX_ROWS = 4

const normalizedQuestions = computed(() => {
  const questions = normalizeQuestions(props.questions)
  // 添加 otherOptionValue 字段
  return questions.map((q) => {
    const otherOption = q.options.find((opt) => isOtherOption(opt))
    return {
      ...q,
      otherOptionValue: otherOption?.value || DEFAULT_OTHER_OPTION_VALUE
    }
  })
})
const isToolApproval = computed(() => props.kind === 'tool_approval')
const activeToolRequest = computed(() => props.actionRequests[activeToolIndex.value] || null)
const activeToolIcon = computed(() => getToolIcon(activeToolRequest.value?.name) || Wrench)

const activeQuestion = computed(() => {
  if (normalizedQuestions.value.length === 0) return null
  const index = Math.min(activeQuestionIndex.value, normalizedQuestions.value.length - 1)
  return normalizedQuestions.value[index]
})

const resetForm = () => {
  isProcessing.value = false
  activeQuestionIndex.value = 0
  selectedValues.value = {}
  otherTexts.value = {}
  toolDecisions.value = {}
  activeToolIndex.value = 0
}

const adjustOtherTextareaHeight = () => {
  const textarea = otherTextareaRef.value
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

const handleOtherTextInput = (questionId, event) => {
  otherTexts.value[questionId] = event.target.value
  adjustOtherTextareaHeight()
}

const setActiveQuestion = (index) => {
  if (isProcessing.value) return
  if (index < 0 || index >= normalizedQuestions.value.length) return
  activeQuestionIndex.value = index
  nextTick(() => {
    adjustOtherTextareaHeight()
  })
}

const syncAnswersWithQuestions = () => {
  const nextSelectedValues = {}
  const nextOtherTexts = {}

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
      } else if (questionItem.options.length > 0) {
        nextSelectedValues[questionId] = [questionItem.options[0].value]
      } else {
        nextSelectedValues[questionId] = []
      }
    }

    const text = String(otherTexts.value[questionId] || '').trim()
    if (text) {
      nextOtherTexts[questionId] = text
    }
  })

  selectedValues.value = nextSelectedValues
  otherTexts.value = nextOtherTexts
}

const getSelected = (questionId) => {
  const selected = selectedValues.value[questionId]
  return Array.isArray(selected) ? selected : []
}

const isQuestionOtherSelected = (questionItem) => {
  const selected = getSelected(questionItem.questionId)
  return selected.includes(questionItem.otherOptionValue)
}

const shouldShowOtherInput = (questionItem) => {
  if (!questionItem || !questionItem.allowOther) return false
  return isQuestionOtherSelected(questionItem)
}

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      activeQuestionIndex.value = 0
      activeToolIndex.value = 0
      nextTick(() => {
        adjustOtherTextareaHeight()
      })
      return
    }

    if (!newVal) {
      resetForm()
    }
  }
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

  const current = getSelected(questionId)
  if (current.includes(value)) {
    selectedValues.value[questionId] = current.filter((item) => item !== value)
  } else {
    selectedValues.value[questionId] = [...current, value]
  }
  nextTick(() => {
    adjustOtherTextareaHeight()
  })
}

const setSingle = (questionId, value) => {
  if (isProcessing.value) return
  selectedValues.value[questionId] = [value]
  nextTick(() => {
    adjustOtherTextareaHeight()
  })
}

const isQuestionAnswered = (questionItem) => {
  const selected = getSelected(questionItem.questionId)
  if (selected.length === 0) return false

  const other = String(otherTexts.value[questionItem.questionId] || '').trim()
  if (questionItem.allowOther && isQuestionOtherSelected(questionItem)) {
    return Boolean(other)
  }

  return true
}

const isSubmitDisabled = computed(() => {
  if (isProcessing.value) return true
  if (normalizedQuestions.value.length === 0) return true

  return normalizedQuestions.value.some((questionItem) => !isQuestionAnswered(questionItem))
})

const isLastQuestion = computed(() => {
  if (normalizedQuestions.value.length === 0) return true
  return activeQuestionIndex.value >= normalizedQuestions.value.length - 1
})

const isCurrentQuestionAnswered = computed(() => {
  if (!activeQuestion.value) return false
  return isQuestionAnswered(activeQuestion.value)
})

const primaryButtonText = computed(() => (isLastQuestion.value ? '提交' : '下一项'))

const isPrimaryButtonDisabled = computed(() => {
  if (isProcessing.value) return true
  if (!activeQuestion.value) return true

  if (isLastQuestion.value) {
    return isSubmitDisabled.value
  }

  return !isCurrentQuestionAnswered.value
})

const buildQuestionAnswer = (questionItem) => {
  const selected = getSelected(questionItem.questionId)
  const other = String(otherTexts.value[questionItem.questionId] || '').trim()

  if (questionItem.allowOther && isQuestionOtherSelected(questionItem)) {
    const selectedWithoutOther = selected.filter((value) => value !== questionItem.otherOptionValue)
    return {
      type: 'other',
      text: other,
      selected: selectedWithoutOther
    }
  }

  if (questionItem.multiSelect) {
    return selected
  }

  return selected[0]
}

const buildAnswer = () => {
  const answer = {}
  normalizedQuestions.value.forEach((questionItem) => {
    answer[questionItem.questionId] = buildQuestionAnswer(questionItem)
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

const handleToolDecision = (decision) => {
  if (isProcessing.value || !activeToolRequest.value) return

  const nextDecisions = { ...toolDecisions.value, [activeToolIndex.value]: decision }
  toolDecisions.value = nextDecisions

  if (activeToolIndex.value < props.actionRequests.length - 1) {
    activeToolIndex.value += 1
    return
  }

  isProcessing.value = true
  emit('submit', {
    decisions: buildToolApprovalDecisions(nextDecisions, props.actionRequests.length)
  })
}

const toolDisplayName = (name) =>
  TOOL_DISPLAY_NAMES[name] || '工具调用'

const formatToolArgs = (args) =>
  typeof args === 'string' ? args : JSON.stringify(args ?? {}, null, 2)

const previewToolArgs = (formattedArgs) => {
  const value = formattedArgs.replace(/\s+/g, ' ')
  return value.length > 96 ? `${value.slice(0, 93)}...` : value
}

const formattedToolArgs = computed(() => formatToolArgs(activeToolRequest.value?.args))
const previewedToolArgs = computed(() => previewToolArgs(formattedToolArgs.value))
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

  &.is-tool-approval {
    max-width: 560px;
  }
}

.approval-content {
  padding: 16px 20px;
}

.question-tabs {
  display: flex;
  flex-wrap: nowrap;
  justify-content: center;
  gap: 8px;
  margin-bottom: 14px;
  width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 2px;
  box-sizing: border-box;
  overscroll-behavior-x: contain;
}

.tab-item {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  width: 36px;
  height: 30px;
  border: 1px solid var(--gray-200);
  background: var(--gray-25);
  color: var(--gray-700);
  border-radius: 8px;
  padding: 0;
  font-size: 12px;
  cursor: pointer;
}

.tab-item:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tab-item.active {
  border-color: var(--main-color);
  background: var(--main-50);
  color: var(--main-700);
}

.tab-item.completed .tab-index {
  color: var(--green-700);
  border-color: var(--green-200);
  background: var(--green-50);
}

.tab-index {
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-600);
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

.tool-approval-header {
  justify-content: space-between;
  margin-bottom: 12px;
}

.tool-approval-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);

  h4 {
    font-size: 15px;
    font-weight: 600;
  }
}

.tool-approval-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
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

.tool-request-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.tool-request-icon {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--color-text-secondary);
}

.tool-request-copy {
  min-width: 0;
  flex: 1;
}

.tool-request-name {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;

  strong {
    color: var(--color-text);
    font-size: 13px;
  }

  code {
    overflow: hidden;
    color: var(--color-text-secondary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.tool-args-preview {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-tertiary);
  text-align: left;
  cursor: help;

  > span,
  > svg {
    flex-shrink: 0;
  }

  code {
    min-width: 0;
    overflow: hidden;
    color: var(--color-text-secondary);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.tool-args-tooltip {
  max-width: 420px;
  max-height: 240px;
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
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
  gap: 8px;
}

.option-item {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--gray-800);
  font-size: 14px;
}

.option-item .recommended {
  color: var(--main-color);
  font-weight: 600;
}

.other-input {
  margin-top: 10px;
}

.other-input textarea {
  width: 100%;
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.5;
  font-family: inherit;
  outline: none;
  resize: none;
  overflow-y: hidden;
  box-sizing: border-box;
}

.other-input textarea:focus {
  border-color: var(--main-color);
}

.approval-actions {
  display: flex;
  gap: 8px;
  padding: 10px 20px 14px;
}

.btn {
  flex: 1;
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

.btn-reject {
  background: var(--gray-100);
  color: var(--gray-700);
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

  .approval-content {
    padding: 12px 16px;
  }

  .tab-item {
    min-width: 30px;
    width: 30px;
    height: 26px;
    padding: 0;
    font-size: 11px;
  }

  .tab-index {
    font-size: 10px;
  }

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

  .btn {
    min-height: 32px;
    padding: 6px 14px;
    font-size: 12px;
  }
}
</style>
