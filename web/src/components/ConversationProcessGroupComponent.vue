<template>
  <section class="conversation-process-group">
    <button
      type="button"
      class="process-summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span>{{ summary }}</span>
    </button>
    <div class="process-collapse-panel" :class="{ 'is-expanded': expanded }">
      <div class="process-collapse-inner">
        <div class="process-content">
          <template v-for="item in items" :key="item.key">
            <AgentMessageComponent
              v-if="item.type === 'message'"
              :message="item.message"
              :hide-tool-calls="true"
              :mention="mention"
            />
            <ToolCallsGroupComponent v-else :tool-calls="item.toolCalls" :entries="item.entries" />
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import AgentMessageComponent from '@/components/AgentMessageComponent.vue'
import ToolCallsGroupComponent from '@/components/ToolCallsGroupComponent.vue'
import { formatProcessDuration } from '@/utils/conversationProcessGrouping.js'

const props = defineProps({
  items: { type: Array, default: () => [] },
  messageCount: { type: Number, default: 0 },
  toolCallCount: { type: Number, default: 0 },
  durationMs: { type: Number, default: null },
  mention: { type: Object, default: () => null }
})

const expanded = ref(false)
const summary = computed(() => formatProcessDuration(props.durationMs))
</script>

<style scoped lang="less">
.conversation-process-group {
  display: flex;
  flex-direction: column;
}

.process-summary {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 30px;
  padding: 4px 0;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  text-align: left;
  transition: color 0.18s ease;

  &:hover,
  &:focus-visible {
    color: var(--gray-700);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 2px;
  }
}

.process-collapse-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition:
    grid-template-rows 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    visibility 0.24s ease;
  visibility: hidden;
  min-width: 0;

  &.is-expanded {
    grid-template-rows: 1fr;
    visibility: visible;
  }
}

.process-collapse-inner {
  overflow: hidden;
  min-height: 0;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.process-collapse-panel.is-expanded .process-collapse-inner {
  opacity: 1;
  transform: translateY(0);
}

.process-content {
  display: flex;
  flex-direction: column;
  padding-top: 8px;
}

@media (prefers-reduced-motion: reduce) {
  .process-collapse-panel,
  .process-collapse-inner {
    transition: none;
  }
}
</style>
