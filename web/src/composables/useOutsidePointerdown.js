import { onBeforeUnmount, onMounted, unref } from 'vue'

/**
 * 在 pointerdown 命中 ignoreRefs 之外时把 openRef 置为 false。
 *
 * 用于在 a-dropdown 之外、仍然需要"点击外部关闭"的轻量浮层（例如
 * 自定义面板）。capture 阶段触发，避免被组件内部的 stopPropagation 阻断。
 *
 * @param {import('vue').Ref<boolean>} openRef 控制显隐的 ref
 * @param {Array<import('vue').Ref<HTMLElement | null>>} ignoreRefs 点击落在这些 ref 节点内不关闭
 */
export function useOutsidePointerdown(openRef, ignoreRefs = []) {
  const handler = (event) => {
    if (!unref(openRef)) return
    const path = event.composedPath()
    const isInside = ignoreRefs.some((ref) => unref(ref) && path.includes(unref(ref)))
    if (!isInside) {
      openRef.value = false
    }
  }

  onMounted(() => document.addEventListener('pointerdown', handler, true))
  onBeforeUnmount(() => document.removeEventListener('pointerdown', handler, true))
}
