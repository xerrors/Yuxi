/** 根据容器高度计算固定状态面板扣除上下边距和边框后的内容最大高度。 */
export const getDockedStatePanelMaxHeight = (containerRect, margin = 8, borderWidth = 1) => {
  if (!containerRect) return null
  return Math.max(0, Math.floor(containerRect.height - (margin + borderWidth) * 2))
}

/** 根据输入区顶边计算悬浮状态面板的可用最大高度。 */
export const getFloatingStatePanelMaxHeight = (
  containerRect,
  inputDockRect,
  topOffset = 8,
  gap = 8
) => {
  if (!containerRect || !inputDockRect) return null
  return Math.max(0, Math.floor(inputDockRect.top - containerRect.top - topOffset - gap))
}
