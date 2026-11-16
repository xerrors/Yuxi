<template>
  <transition name="slide-up">
    <div
      v-if="visible"
      class="approval-modal"
      :class="{ 'is-tool-approval': isToolApproval }"
      :role="isToolApproval ? 'dialog' : undefined"
      :aria-labelledby="isToolApproval ? 'tool-approval-question' : undefined"
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
import { ChevronDown, Wrench } from '@lucide/vue'
import {
  isOtherOption,
  normalizeQuestions,
  DEFAULT_OTHER_OPTION_VALUE
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
const otherTexts = ref({})
const otherTextareaRef = ref(null)
const toolRejectButtonRef = ref(null)
const toolArgsExpanded = ref(false)
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

const resetForm = () => {
  isProcessing.value = false
  activeQuestionIndex.value = 0
  selectedValues.value = {}
  otherTexts.value = {}
  toolArgsExpanded.value = false
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
      toolArgsExpanded.value = false
      nextTick(() => {
        adjustOtherTextareaHeight()
        if (isToolApproval.value) {
          toolRejectButtonRef.value?.focus()
        }
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
  gap: 8px;
}

.option-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--gray-800);
  font-size: 14px;
  cursor: pointer;

  input {
    margin-top: 3px;
    flex-shrink: 0;
  }
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
  color: var(--gray-800);

  &.recommended {
    color: var(--main-color);
    font-weight: 600;
  }
}

.option-description {
  font-size: 12px;
  color: var(--gray-500);
  line-height: 1.45;
  word-break: break-word;
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

.tool-approval-actions {
  justify-content: flex-end;
  padding-top: 4px;

  .btn {
    flex: 0 0 auto;
    min-width: 82px;
  }
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

.btn:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.btn-reject {
  border: 1px solid var(--gray-200);
  background: var(--gray-25);
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

  .approval-modal.is-tool-approval {
    width: 100%;
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
