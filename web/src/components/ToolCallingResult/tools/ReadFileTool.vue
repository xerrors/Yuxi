<template>
  <BaseToolCall :tool-call="toolCall">
    <template #header>
      <div class="sep-header">
        <!-- 特殊处理：SKILL.md 文件显示为 Skill | <父目录名> -->
        <template v-if="skillName">
          <span class="note skill-note">Skill</span>
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
import BaseToolCall from '../BaseToolCall.vue'
import { parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const parsedArgs = computed(() => parseToolCallArgs(props.toolCall))

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
  .tag {
    color: var(--gray-500);
    background-color: transparent;
  }

  // SKILL.md 专用样式
  .skill-note {
    color: var(--main-700);
  }

  .skill-name {
    font-weight: 600;
    color: var(--main-600);
  }
}
</style>
