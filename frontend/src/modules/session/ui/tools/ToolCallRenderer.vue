<template>
  <component
    :is="currentRenderer"
    v-if="currentRenderer"
    :tool-call="toolCall"
    v-bind="toolId in COOPERATION_ACTIONS ? { cooperationView } : {}"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    ref="toolRendererRef"
  />
  <BaseToolCall
    v-else-if="!isHidden"
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
  />
</template>

<script setup>
import { computed, ref } from 'vue'
import BaseToolCall from './BaseToolCall.vue'
import { CooperationTool, TOOL_RENDERERS } from './toolRenderers.js'
import { COOPERATION_ACTIONS } from '@/modules/session/model/cooperationToolView'
import { getToolCallId, isHiddenToolCall } from './toolRegistry'

const props = defineProps({
  cooperationView: { type: Object, default: null },
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

const toolId = computed(() => getToolCallId(props.toolCall))

const currentRenderer = computed(() =>
  toolId.value in COOPERATION_ACTIONS
    ? CooperationTool
    : TOOL_RENDERERS[toolId.value] || null
)
const isHidden = computed(() => isHiddenToolCall(props.toolCall))

const toolRendererRef = ref(null)
const refreshGraph = () => {
  if (toolRendererRef.value && typeof toolRendererRef.value.refreshGraph === 'function') {
    toolRendererRef.value.refreshGraph()
  }
}

defineExpose({ refreshGraph })
</script>

<style lang="less" scoped></style>
