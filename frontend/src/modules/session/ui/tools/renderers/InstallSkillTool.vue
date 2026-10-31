<template>
  <BaseToolCall :tool-call="toolCall" :status="hasFailure ? 'error' : ''" summary-only>
    <template #header>
      <div class="sep-header">
        <span class="note">安装技能</span>
        <span v-if="label" class="separator">|</span>
        <span class="description" :title="label">{{ label }}</span>
        <span v-if="result === '未发现需要安装的 Skill'" class="tag">未发现技能</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs } from '../toolRegistry'
import { getToolCallResultContent } from '@/modules/session/model/toolCallProjection'

const props = defineProps({ toolCall: { type: Object, required: true } })
const args = computed(() => parseToolCallArgs(props.toolCall))
const label = computed(() => args.value.skill_names?.join('、') || args.value.source || '')
const result = computed(() => getToolCallResultContent(props.toolCall))
// install_skill 的失败回执仍是纯文本，只识别 producer 固定的行前缀。
const hasFailure = computed(() => typeof result.value === 'string' &&
  /^(错误：|安装异常：|安装失败 \()/m.test(result.value))
</script>
