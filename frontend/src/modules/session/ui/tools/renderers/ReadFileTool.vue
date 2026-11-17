<template>
  <BaseToolCall :tool-call="toolCall" :hide-params="imageResult">
    <template v-if="imageResult" #icon>
      <ImageIcon :size="15" aria-hidden="true" />
    </template>
    <template #header>
      <div class="sep-header">
        <span v-if="imageResult" class="note">查看图片</span>
        <!-- 特殊处理：SKILL.md 文件显示为 Skill | <父目录名> -->
        <template v-else-if="skillName">
          <span class="note">Skill</span>
          <span class="separator">|</span>
          <span class="description skill-name">{{ skillName }}</span>
        </template>
        <template v-else>
          <span class="note">读取文件</span>
          <span class="separator" v-if="filePath">|</span>
          <span class="description" :title="filePath">
            <span class="code">{{ fileName }}</span>
            <span class="tag" v-if="lineRange">{{ lineRange }}</span>
          </span>
        </template>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import { Image as ImageIcon } from '@lucide/vue'
import BaseToolCall from '../BaseToolCall.vue'
import { getToolCallStatus, parseToolCallArgs } from '../toolRegistry'
import {
  getToolCallResultContent,
  getToolResultParts
} from '@/modules/session/model/toolCallProjection'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parsedArgs = computed(() => parseToolCallArgs(props.toolCall))

const imageResult = computed(() => {
  if (getToolCallStatus(props.toolCall) === 'error') return false
  return getToolResultParts(getToolCallResultContent(props.toolCall)).some((part) => part.image)
})

const filePath = computed(() => parsedArgs.value.file_path || '')

// 仅显示文件名，悬浮时通过 title 显示完整路径
const fileName = computed(() => {
  const path = filePath.value
  if (!path) return ''
  return path.replace(/\\/g, '/').split('/').pop()
})

const lineRange = computed(() => {
  const offset = parsedArgs.value.offset
  const limit = parsedArgs.value.limit
  if (offset !== undefined && limit !== undefined) {
    return `Lines ${offset}-${Number(offset) + Number(limit)}`
  } else if (limit !== undefined) {
    return `First ${limit} lines`
  }
  return ''
})

// 若读取的是 SKILL.md 文件，提取上一级目录名作为技能名称
// 例：mmm/xxx/SKILL.md → skillName = 'xxx'
const skillName = computed(() => {
  const path = filePath.value
  if (!path.endsWith('SKILL.md')) return null
  const parts = path.replace(/\\/g, '/').split('/')
  // 取倒数第二段（SKILL.md 的父目录名）
  return parts.length >= 2 ? parts[parts.length - 2] : null
})
</script>

<style lang="less" scoped>
.sep-header {
  .skill-name {
    font-weight: 600;
  }
}
</style>
