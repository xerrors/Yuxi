import { computed, ref, shallowRef, watch } from 'vue'
import { useUserStore } from '@/modules/identity'
import { useFilePreviewSession } from '@/modules/workspace'
import {
  START_SECTION,
  closeAgentPanelSection as closeSection,
  upsertAgentPanelSection
} from './agentPanelSections'

const defaultPanelRatio = 0.5

/** 根工作区拥有停靠与标签，文件预览按来源 Thread 或个人空间寻址。 */
export function useSessionPanel({ threadId, statePanelOpen, containerWidth }) {
  const user = useUserStore()
  const isFilePanelOpen = ref(false)
  const isAgentPanelMaximized = ref(false)
  const isResizing = ref(false)
  const panelRatio = ref(defaultPanelRatio)
  const dragWidth = ref(null)
  const chatContentContainerRef = ref(null)
  const panelWrapperRef = ref(null)
  const filePreviews = useFilePreviewSession(threadId)
  const accountVersion = ref(0)
  const filesystemRefreshVersions = shallowRef(new Map())
  const agentPanelSections = ref([START_SECTION])
  const agentPanelActiveSectionKey = ref(START_SECTION.key)
  const activeAgentPanelSection = computed(
    () => agentPanelSections.value.find((section) => section.key === agentPanelActiveSectionKey.value)
  )
  const activeAgentPanelPreview = computed(
    () => filePreviews.tabs.value.find((tab) => tab.key === activeAgentPanelSection.value?.previewKey)
  )
  let drag = null

  const readWidth = () =>
    chatContentContainerRef.value?.clientWidth ||
    panelWrapperRef.value?.parentElement?.clientWidth ||
    containerWidth.value

  const widthLimits = (width) => {
    const narrow = width <= 768
    const max = Math.max(0, Math.min(width - (narrow ? 16 : 350), width * 0.75))
    return { min: Math.min(narrow ? 280 : 320, max), max }
  }
  const clampWidth = (pixels, width) => {
    const { min, max } = widthLimits(width)
    return Math.max(min, Math.min(pixels, max))
  }
  const displayedWidth = computed(() => {
    const width = containerWidth.value || readWidth()
    if (isAgentPanelMaximized.value) return width
    return dragWidth.value ?? clampWidth(width * panelRatio.value, width)
  })
  const filePanelWidthStyle = computed(() => {
    if (!isFilePanelOpen.value) return '0px'
    if (displayedWidth.value) return `${displayedWidth.value}px`
    return isAgentPanelMaximized.value ? '100%' : `${panelRatio.value * 100}%`
  })
  const panelWidthPercentage = computed(() =>
    Math.round(100 * (containerWidth.value ? displayedWidth.value / containerWidth.value : panelRatio.value))
  )

  const showFilePanel = () => {
    isFilePanelOpen.value = true
    statePanelOpen.value = false
  }
  const showSection = (section) => {
    agentPanelSections.value = upsertAgentPanelSection(agentPanelSections.value, section)
    agentPanelActiveSectionKey.value = section.key
    showFilePanel()
  }
  const showStartPanel = () => showSection(START_SECTION)
  const restorePanelSelection = () => {
    if (agentPanelActiveSectionKey.value === 'main-session') {
      agentPanelActiveSectionKey.value = agentPanelSections.value[0].key
    }
  }
  const openPanelPreview = (file, sourceThreadId = threadId.value) => {
    const tab = filePreviews.open(file, sourceThreadId)
    if (!tab) return
    showSection({
      key: `file:${tab.key}`,
      type: 'file',
      title: tab.name,
      path: tab.path,
      previewKey: tab.key
    })
  }
  const activateAgentPanelSection = (key) => {
    if (key === 'main-session' ? isAgentPanelMaximized.value : agentPanelSections.value.some((section) => section.key === key)) {
      agentPanelActiveSectionKey.value = key
    }
  }
  const dismissSection = (key) => {
    const next = closeSection(agentPanelSections.value, agentPanelActiveSectionKey.value, key)
    agentPanelSections.value = next.sections
    agentPanelActiveSectionKey.value = next.activeKey
  }
  /** 选择开始页入口时，切到目标标签再移除开始页。 */
  const openStartSection = (section) => {
    showSection(section)
    dismissSection(START_SECTION.key)
  }
  const closeAgentPanelSection = (key) => {
    const section = agentPanelSections.value.find((item) => item.key === key)
    if (section?.previewKey) filePreviews.close(section.previewKey)
    dismissSection(key)
  }
  const closePanelPreviewPath = (path, sourceThreadId = threadId.value) => {
    if (!sourceThreadId) return
    for (const key of filePreviews.closePath(path, sourceThreadId)) dismissSection(`file:${key}`)
  }

  const resetDrag = () => {
    drag = null
    dragWidth.value = null
    isResizing.value = false
  }
  const hideFilePanel = () => {
    isFilePanelOpen.value = false
    isAgentPanelMaximized.value = false
    restorePanelSelection()
    resetDrag()
  }
  watch([threadId, () => user.token], ([, token], [, previousToken]) => {
    if (token !== previousToken) accountVersion.value += 1
    // 文件资源层已释放预览缓存，停靠层同步撤掉同一视图的标签与选中态。
    hideFilePanel()
    statePanelOpen.value = false
    panelRatio.value = defaultPanelRatio
    filesystemRefreshVersions.value = new Map()
    agentPanelSections.value = [START_SECTION]
    agentPanelActiveSectionKey.value = START_SECTION.key
  }, { flush: 'sync' })
  const toggleAgentPanelMaximized = () => {
    if (isResizing.value) return
    isAgentPanelMaximized.value = !isAgentPanelMaximized.value
    if (!isAgentPanelMaximized.value) restorePanelSelection()
  }
  const handleResizingChange = (resizing, clientX = 0) => {
    if (isAgentPanelMaximized.value) return
    if (resizing) {
      const width = readWidth()
      if (!width) return
      dragWidth.value = panelWrapperRef.value?.offsetWidth || displayedWidth.value
      drag = { width, clientX, start: dragWidth.value }
      isResizing.value = true
      return
    }
    if (drag) panelRatio.value = dragWidth.value / drag.width
    resetDrag()
  }
  const handlePanelResize = (clientX) => {
    if (!drag) return
    const desired = drag.start + drag.clientX - clientX
    dragWidth.value = clampWidth(desired, drag.width)
    if (dragWidth.value !== desired) {
      drag.clientX = clientX
      drag.start = dragWidth.value
    }
  }
  const resizePanelBy = (pixels) => {
    if (isResizing.value || isAgentPanelMaximized.value) return
    const width = readWidth()
    if (width) panelRatio.value = clampWidth(displayedWidth.value + pixels, width) / width
  }
  const markThreadFilesChanged = (sourceThreadId) => {
    if (!sourceThreadId) return
    const versions = filesystemRefreshVersions.value
    filesystemRefreshVersions.value = new Map(versions).set(
      sourceThreadId, (versions.get(sourceThreadId) || 0) + 1
    )
  }

  return {
    accountVersion,
    isFilePanelOpen,
    isAgentPanelMaximized,
    isResizing,
    panelWidthPercentage,
    chatContentContainerRef,
    panelWrapperRef,
    filePanelWidthStyle,
    agentPanelPreviewCache: filePreviews.cache,
    filesystemRefreshVersions,
    markThreadFilesChanged,
    agentPanelSections,
    agentPanelActiveSectionKey,
    activeAgentPanelSection,
    activeAgentPanelPreview,
    showFilePanel,
    showSection,
    showStartPanel,
    openStartSection,
    openPanelPreview,
    activateAgentPanelSection,
    closeAgentPanelSection,
    closePanelPreviewPath,
    hideFilePanel,
    toggleAgentPanelMaximized,
    handleResizingChange,
    handlePanelResize,
    resizePanelBy
  }
}
