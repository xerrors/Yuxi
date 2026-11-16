/** 判断滚动容器是否位于底部附近，容忍浏览器亚像素误差。 */
export function isLogContainerAtBottom(container, threshold = 4) {
  if (!container) return true
  const distance = container.scrollHeight - container.clientHeight - container.scrollTop
  return distance <= threshold
}
