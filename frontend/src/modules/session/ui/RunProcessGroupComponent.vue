<template>
  <section class="session-process-group">
    <button
      type="button"
      class="process-summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span>{{ summary }}</span>
      <ChevronDown :size="14" class="process-chevron" aria-hidden="true" />
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
              :widget-action="widgetAction"
              :widgets-disabled="widgetsDisabled"
            />
            <ToolCallsGroupComponent v-else :cooperation-view="cooperationView" :tool-calls="item.toolCalls" :entries="item.entries" />
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ChevronDown } from '@lucide/vue'
import AgentMessageComponent from '@/modules/session/ui/AgentMessageComponent.vue'
import ToolCallsGroupComponent from '@/modules/session/ui/ToolCallsGroupComponent.vue'
import { formatProcessDuration } from '@/modules/session/model/runProcessGrouping.js'

const props = defineProps({
  widgetAction: { type: Function, default: null },
  widgetsDisabled: Boolean,
  cooperationView: { type: Object, default: null },
  items: { type: Array, default: () => [] },
  durationMs: { type: Number, default: null },
  mention: { type: Object, default: () => null }
})

const expanded = ref(false)
const summary = computed(() => formatProcessDuration(props.durationMs))
</script>

<style scoped lang="less">
.session-process-group {
  display: flex;
  flex-direction: column;
  margin-bottom: 6px;
}

.process-summary {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--gray-600);
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

.process-chevron {
  flex-shrink: 0;
  opacity: 0;

  .process-summary[aria-expanded='false'] & {
    transform: rotate(-90deg);
  }

  .process-summary:is(:hover, :focus-visible, [aria-expanded='true']) & {
    opacity: 1;
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
}

@media (prefers-reduced-motion: reduce) {
  .process-collapse-panel,
  .process-collapse-inner {
    transition: none;
  }
}
</style>
