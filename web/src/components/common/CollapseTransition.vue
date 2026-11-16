<template>
  <Transition
    name="yuxi-collapse"
    @before-enter="beforeEnter"
    @enter="enter"
    @after-enter="reset"
    @enter-cancelled="reset"
    @before-leave="beforeLeave"
    @leave="leave"
    @after-leave="reset"
    @leave-cancelled="reset"
  >
    <slot />
  </Transition>
</template>

<script setup>
const beforeEnter = (el) => {
  el.dataset.oldPaddingTop = el.style.paddingTop
  el.dataset.oldPaddingBottom = el.style.paddingBottom
  el.style.height = '0'
  el.style.paddingTop = '0'
  el.style.paddingBottom = '0'
}

const enter = (el) => {
  el.style.paddingTop = el.dataset.oldPaddingTop
  el.style.paddingBottom = el.dataset.oldPaddingBottom
  const expandedHeight = el.scrollHeight
  el.style.paddingTop = '0'
  el.style.paddingBottom = '0'
  void el.offsetHeight
  el.style.height = expandedHeight > 0 ? `${expandedHeight}px` : ''
  el.style.paddingTop = el.dataset.oldPaddingTop
  el.style.paddingBottom = el.dataset.oldPaddingBottom
}

const beforeLeave = (el) => {
  el.style.height = `${el.scrollHeight}px`
}

const leave = (el) => {
  if (el.scrollHeight === 0) return
  // 强制 reflow，确保从当前高度开始过渡
  void el.offsetHeight
  el.style.height = '0'
  el.style.paddingTop = '0'
  el.style.paddingBottom = '0'
}

const reset = (el) => {
  el.style.height = ''
  el.style.paddingTop = ''
  el.style.paddingBottom = ''
}
</script>

<style lang="less">
.yuxi-collapse-enter-active,
.yuxi-collapse-leave-active {
  transition:
    height 0.25s ease,
    padding-top 0.25s ease,
    padding-bottom 0.25s ease,
    opacity 0.25s ease;
  overflow: hidden;
}

.yuxi-collapse-enter-from,
.yuxi-collapse-leave-to {
  opacity: 0;
}
</style>
