<template>
  <a-dropdown
    :open="open"
    :disabled="disabled"
    :trigger="['click']"
    :placement="
      upward
        ? align === 'end'
          ? 'topRight'
          : 'topLeft'
        : align === 'end'
          ? 'bottomRight'
          : 'bottomLeft'
    "
    :align="{
      offset: [horizontalOffset, upward ? -4 : 4],
      overflow: { adjustX: 0, adjustY: upward ? 0 : 1 }
    }"
    :min-overlay-width-match-trigger="false"
    overlay-class-name="config-dropdown-overlay"
    @open-change="setOpen"
  >
    <span
      ref="triggerRef"
      class="action-dropdown-anchor"
      :class="{ 'is-block': block }"
      @keydown.esc.stop.prevent="closeAndFocus"
    >
      <slot name="trigger" :open="open" />
    </span>
    <template #overlay>
      <div
        class="action-dropdown-positioner"
        :class="{ 'is-upward': upward }"
        :style="{ width: `${panelWidth}px` }"
      >
        <div
          ref="panelRef"
          class="config-dropdown-panel action-dropdown-panel"
          :class="{ 'is-compact': panelHeight < 240 }"
          :style="{ width: `${panelWidth}px`, maxHeight: `${panelHeight}px` }"
          @click.stop
          @keydown.esc.stop.prevent="closeAndFocus"
        >
          <div v-if="searchPlaceholder" class="action-dropdown-search">
            <a-input
              :value="search"
              :placeholder="searchPlaceholder"
              :aria-label="searchPlaceholder"
              allow-clear
              autocomplete="off"
              @update:value="emit('update:search', $event)"
              @keydown="handleSearchKeydown"
            >
              <template #prefix><Search :size="14" /></template>
              <template v-if="$slots['search-suffix']" #suffix
                ><slot name="search-suffix"
              /></template>
            </a-input>
          </div>
          <div class="action-dropdown-list"><slot /></div>
          <div v-if="$slots.footer" class="action-dropdown-footer"><slot name="footer" /></div>
        </div>
      </div>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useElementBounding, useElementVisibility, useEventListener } from '@vueuse/core'
import { Search } from '@lucide/vue'
import { useOutsidePointerdown } from '@/shared/lib/useOutsidePointerdown'

const props = defineProps({
  open: { type: Boolean, default: false },
  upward: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  search: { type: String, default: '' },
  searchPlaceholder: { type: String, default: '' },
  width: { type: Number, default: 260 },
  align: { type: String, default: 'start' },
  block: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'update:search'])
const triggerRef = ref(null)
const panelRef = ref(null)
const { top, bottom, left, right, update } = useElementBounding(triggerRef)
const viewportTop = ref(0)
const viewportHeight = ref(0)
const viewportLeft = ref(0)
const viewportWidth = ref(0)
const panelWidth = computed(() => Math.max(0, Math.min(props.width, viewportWidth.value - 24)))
const horizontalOffset = computed(() => {
  const anchorLeft = props.align === 'end' ? right.value - panelWidth.value : left.value
  const rightLimit = viewportLeft.value + viewportWidth.value - panelWidth.value - 12
  return Math.max(viewportLeft.value + 12, Math.min(anchorLeft, rightLimit)) - anchorLeft
})
const panelHeight = computed(() => {
  const above = top.value - viewportTop.value - 12
  const below = viewportTop.value + viewportHeight.value - bottom.value - 12
  return Math.max(0, Math.min(420, props.upward ? above : Math.max(above, below)))
})

/** 软键盘或可视区域移动后，保留菜单上方的可见边界。 */
function measureViewport() {
  viewportTop.value = globalThis.window?.visualViewport?.offsetTop || 0
  viewportLeft.value = globalThis.window?.visualViewport?.offsetLeft || 0
  viewportWidth.value =
    globalThis.window?.visualViewport?.width || globalThis.window?.innerWidth || 0
  viewportHeight.value =
    globalThis.window?.visualViewport?.height || globalThis.window?.innerHeight || 0
  update()
}
useEventListener(globalThis.window?.visualViewport, ['resize', 'scroll'], measureViewport)
useEventListener('resize', measureViewport)
const openState = computed({ get: () => props.open, set: (value) => emit('update:open', value) })

/** 在打开前测量可用空间，输入区由 upward 锁定方向。 */
function setOpen(value) {
  if (value && props.disabled) return
  measureViewport()
  openState.value = value
}

/** Escape 关闭后将键盘焦点交还触发按钮。 */
function closeAndFocus() {
  openState.value = false
  triggerRef.value?.querySelector('button')?.focus()
}

/** 搜索框消费键盘事件，并将 Escape 作为弹层关闭操作处理。 */
function handleSearchKeydown(event) {
  event.stopPropagation()
  if (event.key === 'Escape') {
    event.preventDefault()
    closeAndFocus()
  }
}

const panelVisible = useElementVisibility(panelRef)
watch([() => props.open, panelVisible], ([open, visible]) => {
  if (open && visible) panelRef.value?.querySelector('input, button:not(:disabled)')?.focus()
})
watch(
  () => props.open,
  () => emit('update:search', '')
)
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) openState.value = false
  }
)
useOutsidePointerdown(openState, [triggerRef, panelRef])
</script>

<style scoped lang="less">
.action-dropdown-anchor {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  &.is-block {
    display: flex;
    width: 100%;
  }
}

// 固定底边的零高定位容器让内容增减直接向上布局，不等待 Align 再测量高度。
.action-dropdown-positioner.is-upward {
  position: relative;
  height: 0;

  > .action-dropdown-panel {
    position: absolute;
    bottom: 0;
    left: 0;
  }
}

.config-dropdown-panel.action-dropdown-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: calc(100vw - 24px);
  overflow: hidden;

  // 顶部表单与低视口统一滚动，避免搜索和页脚挤掉选项。
  &.is-compact {
    display: block;
    overflow-y: auto;
    overscroll-behavior: contain;
    .action-dropdown-list {
      overflow: visible;
    }
  }
}

.action-dropdown-search {
  flex-shrink: 0;
  padding: 4px 4px 8px;

  :deep(.ant-input-affix-wrapper) {
    border-color: var(--gray-0);
    background: var(--gray-25);
  }
  :deep(.ant-input) {
    background: transparent;
  }
  :deep(.ant-input-prefix) {
    color: var(--gray-500);
  }
}

.action-dropdown-list {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.action-dropdown-footer {
  flex-shrink: 0;
}
</style>
