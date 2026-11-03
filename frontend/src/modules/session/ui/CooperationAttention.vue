<template>
  <div class="cooperation-attention">
    <span class="attention-announcement" role="status" aria-live="polite" aria-atomic="true">
      {{ announcement }}
    </span>
    <div v-if="actions.length" class="attention-card" :class="{ 'is-expanded': expanded }">
      <button
        ref="pillRef"
        type="button"
        class="attention-pill"
        :aria-label="announcement"
        :aria-expanded="actions.length > 1 ? expanded : undefined"
        :aria-controls="expanded ? listId : undefined"
        @keydown.esc.stop.prevent="closeList"
        @click="handleClick"
      >
        <CircleHelp class="attention-icon" :size="16" aria-hidden="true" />
        <span class="attention-summary" :title="announcement">{{ summary }}</span>
        <span class="attention-action">{{
          actions.length === 1 ? actions[0].action : expanded ? '收起待办' : '查看待办'
        }}</span>
        <ChevronDown
          v-if="actions.length > 1"
          class="attention-chevron"
          :class="{ 'is-expanded': expanded }"
          :size="14"
          aria-hidden="true"
        />
        <ArrowRight v-else :size="14" aria-hidden="true" />
      </button>
      <CollapseTransition @after-enter="focusList">
        <div v-if="expanded" class="attention-list-collapse">
          <div
            :id="listId"
            ref="listRef"
            class="attention-list"
            role="group"
            aria-label="协作会话待办"
            @keydown.esc.stop.prevent="closeList"
          >
            <button
              v-for="item in actions"
              :key="item.sessionId"
              type="button"
              class="attention-item"
              :aria-label="`${item.name} ${item.label}，${item.action}`"
              @click="openAction(item)"
            >
              <span class="attention-name" :title="item.name">{{ item.name }}</span>
              <span class="attention-label">{{ item.label }}</span>
              <span class="attention-action">
                {{ item.action }} <ArrowRight :size="14" aria-hidden="true" />
              </span>
            </button>
          </div>
        </div>
      </CollapseTransition>
    </div>
    <div v-if="error" class="attention-error">
      <span>协作状态更新失败{{ actions.length ? '，显示最近已知待办' : '' }}</span>
      <button type="button" :title="error" @click="emit('refresh')">重试</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, useId, watch } from 'vue'
import { ArrowRight, ChevronDown, CircleHelp } from '@lucide/vue'
import CollapseTransition from '@/shared/ui/CollapseTransition.vue'

const props = defineProps({
  actions: { type: Array, default: () => [] },
  error: { type: String, default: '' }
})
const emit = defineEmits(['open', 'refresh'])
const expanded = ref(false)
const listId = useId()
const listRef = ref(null)
const pillRef = ref(null)
watch(() => props.actions.length, (count) => {
  if (count <= 1) expanded.value = false
})
const summary = computed(() => {
  if (props.actions.length === 1) {
    const item = props.actions[0]
    return `${item.name} ${item.label}`
  }
  const approvals = props.actions.filter((item) => item.label === '待审批').length
  const answers = props.actions.length - approvals
  const counts = []
  if (approvals) counts.push(`${approvals} 待审批`)
  if (answers) counts.push(`${answers} 待回答`)
  return `${props.actions.length} 个协作任务需要你处理 · ${counts.join(' / ')}`
})
const announcement = computed(() => (props.actions.length ? summary.value : '没有待处理的协作请求'))

/** 打开目标会话，保留待办直到持久状态更新。 */
function openAction(item) {
  expanded.value = false
  emit('open', item.sessionId)
}

/** 单项直达，多项在胶囊原位展开或收起。 */
function handleClick() {
  if (props.actions.length === 1) openAction(props.actions[0])
  else expanded.value = !expanded.value
}

/** 展开过渡完成后聚焦首项，保留输入区的滚动位置。 */
function focusList() {
  if (expanded.value) listRef.value?.querySelector('button')?.focus({ preventScroll: true })
}

/** 键盘收起卡片后返回触发入口。 */
function closeList() {
  expanded.value = false
  pillRef.value?.focus()
}
</script>

<style scoped lang="less">
.cooperation-attention {
  min-width: 0;
  &:has(.attention-pill),
  &:has(.attention-error) {
    margin-bottom: 8px;
  }
}
.attention-announcement {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.attention-pill,
.attention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  min-height: 40px;
  padding: 8px 12px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }
  > svg {
    flex-shrink: 0;
  }
}
.attention-pill {
  width: 100%;
  border: 0;
  border-radius: inherit;
  background: transparent;
  color: var(--color-text-secondary);
  &:hover {
    background: var(--gray-25);
  }
  .attention-summary {
    flex: 1;
    text-align: left;
  }
  .attention-action {
    color: var(--color-text);
  }
}
.attention-card {
  width: fit-content;
  max-width: 100%;
  border: 1px solid var(--gray-150);
  border-radius: 22px;
  background: var(--gray-0);
  transition: border-radius 0.2s ease;

  &.is-expanded {
    width: 100%;
    border-radius: 16px;

    .attention-pill {
      border-radius: 15px 15px 0 0;
    }
  }
}
.attention-chevron {
  transition: transform 0.2s ease;

  &.is-expanded {
    transform: rotate(180deg);
  }
}
.attention-list-collapse {
  &.yuxi-collapse-enter-active,
  &.yuxi-collapse-leave-active {
    transition-duration: 0.2s;
  }
}
.attention-icon {
  color: var(--color-warning-700);
}
.attention-summary,
.attention-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.attention-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  font-weight: 500;
}
.attention-list {
  max-height: min(280px, 30vh);
  padding: 4px 8px 8px;
  border-top: 1px solid var(--gray-100);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.attention-item {
  width: 100%;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  &:hover {
    background: var(--gray-50);
  }
  .attention-name {
    flex: 1;
    text-align: left;
  }
  .attention-action {
    color: var(--main-color);
  }
}
.attention-label {
  flex-shrink: 0;
  color: var(--color-warning-900);
}
.attention-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
  button {
    flex-shrink: 0;
    border: 0;
    background: transparent;
    color: var(--main-color);
    cursor: pointer;
  }
}
@media (prefers-reduced-motion: reduce) {
  .attention-card,
  .attention-chevron,
  .attention-list-collapse.yuxi-collapse-enter-active,
  .attention-list-collapse.yuxi-collapse-leave-active {
    transition: none;
  }
}
</style>
