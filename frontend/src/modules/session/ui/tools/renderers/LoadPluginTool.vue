<template>
  <BaseToolCall :tool-call="toolCall">
    <template v-if="['completed', 'error', 'incomplete'].includes(status)" #icon>
      <component
        :is="icon"
        size="15"
        aria-hidden="true"
        class="tool-loader"
      />
    </template>
    <template #header>
      <div class="sep-header">
        <span class="note">加载插件</span>
        <span v-if="label" class="separator">|</span>
        <span class="description" :title="label">{{ label }}</span>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed, inject } from 'vue'
import { Puzzle } from '@lucide/vue'
import {
  getAgentConfigOptionLabel,
  getAgentConfigOptionValue,
  getAgentConfigOptions,
  useAgentStore
} from '@/modules/agents'
import { getSkillIcon } from '@/shared/lib/skill_icon_utils'
import BaseToolCall from '../BaseToolCall.vue'
import { getToolCallStatus, parseToolCallArgs } from '../toolRegistry'

const props = defineProps({ toolCall: { type: Object, required: true } })
const agentStore = useAgentStore()
const configurableItems = inject('sessionAgentConfigurableItems', computed(() => agentStore.configurableItems))
const status = computed(() => getToolCallStatus(props.toolCall))
const pluginId = computed(() => {
  const value = parseToolCallArgs(props.toolCall)?.plugin_id
  return typeof value === 'string' ? value : ''
})
const label = computed(() => {
  const option = getAgentConfigOptions(configurableItems.value.plugins)
    .find((item) => getAgentConfigOptionValue(item) === pluginId.value)
  return getAgentConfigOptionLabel(option) || pluginId.value
})
const icon = computed(() => getSkillIcon(`${pluginId.value} ${label.value}`, Puzzle))
</script>
