<template>
  <BaseToolCall
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    :force-show-result="questions.length > 0"
    hide-params
  >
    <template #header>
      <div class="sep-header">
        <span class="note">提问</span>
        <span class="separator">|</span>
        <span class="description">{{ shortQuestionSummary }}</span>
        <span v-if="displayAnswer" class="tag tag-answered"> 已回答: {{ displayAnswer }} </span>
      </div>
    </template>

    <template #result>
      <div class="ask-question-result">
        <div v-if="questions.length" class="question-list">
          <div
            v-for="(questionItem, questionIndex) in questions"
            :key="questionItem.questionId || questionIndex"
            class="question-item"
          >
            <div class="question-title">
              <span class="question-index">{{ questionIndex + 1 }}</span>
              <span class="question-text">{{ questionItem.question }}</span>
            </div>

            <div v-if="questionItem.operation" class="operation-row">
              <span class="row-label">操作</span>
              <span class="operation-text">{{ questionItem.operation }}</span>
            </div>

            <div v-if="questionItem.options && questionItem.options.length" class="options-row">
              <span class="row-label">选项</span>
              <div class="options-list">
                <div
                  v-for="(opt, optIdx) in questionItem.options"
                  :key="`${opt.value}-${optIdx}`"
                  class="option-pill"
                  :class="{
                    'is-recommended': optIdx === 0 && String(opt.label).includes('(Recommended)')
                  }"
                >
                  <div class="option-pill-main">
                    <span class="option-pill-label">{{ opt.label }}</span>
                    <span v-if="opt.description" class="option-pill-desc">{{
                      opt.description
                    }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="getQuestionAnswerText(questionItem)" class="answer-row">
              <span class="row-label">回答</span>
              <span class="answer-text">{{ getQuestionAnswerText(questionItem) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="no-question">暂无提问内容</div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { normalizeQuestions } from '@/utils/questionUtils'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  },
  appearance: {
    type: String,
    default: 'card'
  },
  defaultExpanded: {
    type: Boolean,
    default: false
  }
})

const parseJsonValue = (value, fallback = null) => {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'object') return value
  try {
    return JSON.parse(value)
  } catch {
    return fallback
  }
}

const parsedArgs = computed(() => {
  return parseToolCallArgs(props.toolCall)
})

const parsedResult = computed(() => {
  const content = props.toolCall.tool_call_result?.content ?? props.toolCall.result
  return parseJsonValue(content, null)
})

const questions = computed(() => {
  const args = parsedArgs.value
  const fromArgs = normalizeQuestions(args?.questions ?? args)
  if (fromArgs.length) return fromArgs

  const result = parsedResult.value
  const fromResult = normalizeQuestions(result?.questions ?? result)
  if (fromResult.length) return fromResult

  return []
})

const shortQuestionSummary = computed(() => {
  if (!questions.value.length) return '无问题'

  const firstQuestion = questions.value[0].question
  const shortFirstQuestion =
    firstQuestion.length > 36 ? firstQuestion.slice(0, 36) + '...' : firstQuestion

  if (questions.value.length === 1) return shortFirstQuestion
  return `${shortFirstQuestion} 等 ${questions.value.length} 题`
})

const userAnswer = computed(() => {
  const result = parsedResult.value
  if (!result) return null
  return result.user_answer || result.answer || null
})

const getOptionLabel = (value, questionItem) => {
  const rawValue = String(value || '').trim()
  if (!rawValue) return ''

  const option = questionItem?.options?.find(
    (item) => String(item.value) === rawValue || String(item.label) === rawValue
  )
  return option?.label || rawValue
}

const formatSingleAnswer = (answer, questionItem) => {
  if (Array.isArray(answer)) {
    return answer
      .map((item) => getOptionLabel(item, questionItem))
      .filter(Boolean)
      .join('、')
  }

  if (answer && typeof answer === 'object') {
    if (answer.type === 'other') {
      const selected = Array.isArray(answer.selected)
        ? answer.selected.map((item) => getOptionLabel(item, questionItem)).filter(Boolean)
        : []
      const text = String(answer.text || '').trim()
      return [...selected, text ? `其他: ${text}` : '其他'].join('、')
    }
    return JSON.stringify(answer)
  }

  return getOptionLabel(answer, questionItem)
}

const getQuestionAnswer = (questionItem) => {
  const answer = userAnswer.value
  if (answer === null || answer === undefined || answer === '') return undefined

  if (questions.value.length === 1 && (typeof answer !== 'object' || Array.isArray(answer))) {
    return answer
  }

  if (answer && typeof answer === 'object' && !Array.isArray(answer)) {
    if (Object.prototype.hasOwnProperty.call(answer, questionItem.questionId)) {
      return answer[questionItem.questionId]
    }

    if (questions.value.length === 1 && answer.type === 'other') {
      return answer
    }
  }

  return undefined
}

const getQuestionAnswerText = (questionItem) => {
  const answer = getQuestionAnswer(questionItem)
  if (answer === undefined || answer === null || answer === '') return ''
  return formatSingleAnswer(answer, questionItem)
}

const displayAnswer = computed(() => {
  if (!userAnswer.value) return ''

  const summaries = questions.value
    .map((questionItem) => getQuestionAnswerText(questionItem))
    .filter(Boolean)

  if (!summaries.length) {
    const text = formatSingleAnswer(userAnswer.value, questions.value[0])
    return text.length > 120 ? text.slice(0, 120) + '...' : text
  }

  const summary = summaries.join(' | ')
  return summary.length > 120 ? summary.slice(0, 120) + '...' : summary
})
</script>

<style lang="less" scoped>
.sep-header {
  .tag-answered {
    margin-left: 8px;
    color: var(--color-success-700);
    background: var(--color-success-50);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 45%;
    flex-shrink: 0;
  }
}

.ask-question-result {
  padding: 8px 0;

  .question-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .question-item {
    border: 1px solid var(--gray-100);
    background: var(--gray-25);
    border-radius: 6px;
    padding: 10px 12px;
  }

  .question-title {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: var(--gray-800);
    font-size: 13px;
    line-height: 1.5;
  }

  .question-index {
    width: 20px;
    height: 20px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 20px;
    border-radius: 50%;
    color: var(--main-700);
    background: var(--main-50);
    font-size: 12px;
    font-weight: 600;
  }

  .question-text {
    min-width: 0;
    word-break: break-word;
  }

  .operation-row,
  .options-row,
  .answer-row {
    margin-top: 8px;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 12px;
    line-height: 1.5;
  }

  .row-label {
    color: var(--gray-500);
    flex: 0 0 auto;
  }

  .operation-text,
  .answer-text {
    min-width: 0;
    color: var(--gray-700);
    word-break: break-word;
  }

  .options-list {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .option-pill {
    background: var(--gray-50);
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    padding: 6px 10px;

    &.is-recommended {
      border-color: var(--main-200);
      background: var(--main-25);
    }
  }

  .option-pill-main {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .option-pill-label {
    color: var(--gray-800);
    font-weight: 500;
  }

  .option-pill-desc {
    color: var(--gray-500);
    font-size: 11px;
    line-height: 1.4;
    word-break: break-word;
  }

  .answer-row {
    padding-top: 8px;
    border-top: 1px solid var(--gray-100);
  }

  .answer-text {
    color: var(--color-success-700);
    font-weight: 500;
  }

  .no-question {
    padding: 12px;
    color: var(--gray-500);
    font-size: 13px;
    text-align: center;
  }
}
</style>
