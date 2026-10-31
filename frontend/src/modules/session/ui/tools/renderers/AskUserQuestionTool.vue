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
        <span v-if="displayAnswer" class="tag tag-answered">
          已回答<span v-if="questions.length === 1">: {{ displayAnswer }}</span>
        </span>
      </div>
    </template>

    <template #result>
      <div class="ask-question-result">
        <div v-if="questions.length" class="answer-list">
          <div
            v-for="(questionItem, questionIndex) in questions"
            :key="questionItem.questionId || questionIndex"
            class="answer-item"
          >
            <div v-if="questions.length > 1" class="question-title">
              <span class="question-index">{{ questionIndex + 1 }}.</span>
              <span>{{ questionItem.question }}</span>
            </div>
            <div class="answer-row">
              <span class="row-label">回答</span>
              <span class="answer-text">{{ getQuestionAnswerText(questionItem) || '尚未回答' }}</span>
            </div>
          </div>
        </div>
        <div v-else class="no-answer">暂无回答</div>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { mapQuestionsForDisplay } from '@/modules/session/model/questionUtils'
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
  // 执行结果包含入口补齐的 ID；等待期间只展示模型原始参数。
  const result = parsedResult.value
  if (Array.isArray(result?.questions)) return mapQuestionsForDisplay(result.questions)
  return mapQuestionsForDisplay(parsedArgs.value?.questions)
})

const shortQuestionSummary = computed(() => {
  if (!questions.value.length) return '无问题'
  if (questions.value.length > 1) return `${questions.value.length} 个问题`

  const firstQuestion = questions.value[0].question
  return firstQuestion.length > 36 ? firstQuestion.slice(0, 36) + '...' : firstQuestion
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

  .answer-list {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .question-title {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 4px;
    color: var(--color-text);
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .question-index {
    flex: 0 0 auto;
    color: var(--color-text-secondary);
  }

  .answer-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    font-size: 13px;
    line-height: 1.6;
  }

  .row-label {
    color: var(--color-text-secondary);
    flex: 0 0 auto;
  }

  .answer-text {
    min-width: 0;
    color: var(--color-text);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .no-answer {
    color: var(--color-text-secondary);
    font-size: 13px;
  }
}
</style>
