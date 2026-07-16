<template>
  <div
    class="input-box"
    :class="[customClasses, { 'dragging-files': isDraggingFiles }]"
    @click="handleInputBoxClick"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <div class="top-slot">
      <slot name="top"></slot>
    </div>

    <div class="expand-options" v-if="hasOptionsLeft">
      <a-dropdown
        v-model:open="optionsExpanded"
        :trigger="['click']"
        placement="topLeft"
        overlay-class-name="config-dropdown-overlay"
      >
        <template #overlay>
          <div ref="optionsPanelRef" class="options-dropdown-content">
            <slot name="options-left">
              <div class="no-options">没有配置 options</div>
            </slot>
          </div>
        </template>
        <button
          ref="optionsTriggerRef"
          type="button"
          class="expand-btn"
          :class="{ active: optionsExpanded }"
          aria-label="添加内容"
          aria-haspopup="menu"
          :aria-expanded="optionsExpanded"
        >
          <Plus :size="17" />
        </button>
      </a-dropdown>
      <slot name="actions-left"></slot>
    </div>

    <div
      ref="inputRef"
      class="user-input mention-editor"
      role="textbox"
      aria-multiline="true"
      :aria-label="placeholder"
      :contenteditable="disabled ? 'false' : 'true'"
      :data-placeholder="placeholder"
      @keydown="handleKeyPress"
      @keyup="handleKeyUp"
      @input="handleInput"
      @focus="focusInput"
      @click="handleEditorClick"
      @paste="handlePaste"
      @compositionstart="handleCompositionStart"
      @compositionend="handleCompositionEnd"
    ></div>

    <!-- @ 提及选择弹窗 -->
    <div v-if="mentionPopupVisible" ref="mentionDropdownRef" class="mention-dropdown-wrapper">
      <div class="mention-popup" @mousedown.prevent>
        <!-- 文件列表 -->
        <div v-if="mentionItems.files.length > 0 || showFileSearchPrompt" class="mention-group">
          <div class="mention-group-title">文件</div>
          <div v-if="showFileSearchPrompt" class="mention-search-placeholder">
            输入相关内容以搜索文件
          </div>
          <template v-else>
            <div
              v-for="(item, index) in mentionItems.files"
              :key="'file-' + item.value"
              :class="['mention-item', 'file-item', { active: isItemSelected('file', index) }]"
              @click="insertMention(item)"
            >
              <div class="file-info-left">
                <FileTypeIcon
                  :name="item.label"
                  :is-dir="item.is_dir"
                  :size="16"
                  class="file-type-icon"
                />
                <span class="file-name" :title="item.label">
                  <span
                    v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                    :key="pIdx"
                    :class="{ 'query-match': part.isMatch }"
                    >{{ part.text }}</span
                  >
                </span>
              </div>
              <span
                v-if="formatMentionPath(item.description)"
                class="file-parent-dir"
                :title="formatMentionPath(item.description)"
              >
                <span
                  v-for="(part, pIdx) in splitTextByQuery(
                    formatMentionPath(item.description),
                    mentionQuery
                  )"
                  :key="pIdx"
                  :class="{ 'query-match': part.isMatch }"
                  >{{ part.text }}</span
                >
              </span>
            </div>
          </template>
        </div>

        <!-- 知识库列表 -->
        <div v-if="mentionItems.knowledgeBases.length > 0" class="mention-group">
          <div class="mention-group-title">知识库</div>
          <div
            v-for="(item, index) in mentionItems.knowledgeBases"
            :key="'kb-' + item.value"
            :class="[
              'mention-item',
              'resource-item',
              { active: isItemSelected('knowledge', index) }
            ]"
            @click="insertMention(item)"
          >
            <div class="resource-name">
              <span
                v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
            <div
              v-if="getMentionDescription(item.description)"
              class="resource-description"
              :title="getMentionDescription(item.description)"
            >
              <span
                v-for="(part, pIdx) in splitTextByQuery(
                  getMentionDescription(item.description),
                  mentionQuery
                )"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
          </div>
        </div>

        <!-- MCP 列表 -->
        <div v-if="mentionItems.mcps.length > 0" class="mention-group">
          <div class="mention-group-title">MCP</div>
          <div
            v-for="(item, index) in mentionItems.mcps"
            :key="'mcp-' + item.value"
            :class="['mention-item', 'resource-item', { active: isItemSelected('mcp', index) }]"
            @click="insertMention(item)"
          >
            <div class="resource-name">
              <span
                v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
            <div
              v-if="getMentionDescription(item.description)"
              class="resource-description"
              :title="getMentionDescription(item.description)"
            >
              <span
                v-for="(part, pIdx) in splitTextByQuery(
                  getMentionDescription(item.description),
                  mentionQuery
                )"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
          </div>
        </div>

        <!-- Skills 列表 -->
        <div v-if="mentionItems.skills.length > 0" class="mention-group">
          <div class="mention-group-title">Skills</div>
          <div
            v-for="(item, index) in mentionItems.skills"
            :key="'skill-' + item.value"
            :class="['mention-item', 'resource-item', { active: isItemSelected('skill', index) }]"
            @click="insertMention(item)"
          >
            <div class="resource-name">
              <span
                v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
            <div
              v-if="getMentionDescription(item.description)"
              class="resource-description"
              :title="getMentionDescription(item.description)"
            >
              <span
                v-for="(part, pIdx) in splitTextByQuery(
                  getMentionDescription(item.description),
                  mentionQuery
                )"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
          </div>
        </div>

        <!-- Subagents 列表 -->
        <div v-if="mentionItems.subagents.length > 0" class="mention-group">
          <div class="mention-group-title">Subagents</div>
          <div
            v-for="(item, index) in mentionItems.subagents"
            :key="'subagent-' + item.value"
            :class="[
              'mention-item',
              'resource-item',
              { active: isItemSelected('subagent', index) }
            ]"
            @click="insertMention(item)"
          >
            <div class="resource-name">
              <span
                v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
            <div
              v-if="getMentionDescription(item.description)"
              class="resource-description"
              :title="getMentionDescription(item.description)"
            >
              <span
                v-for="(part, pIdx) in splitTextByQuery(
                  getMentionDescription(item.description),
                  mentionQuery
                )"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
                >{{ part.text }}</span
              >
            </div>
          </div>
        </div>

        <!-- 无结果 -->
        <div v-if="!hasAnyItems" class="mention-empty">暂无可引用的项</div>
      </div>
    </div>

    <div class="send-button-container">
      <slot name="actions-right"></slot>
      <a-tooltip :title="isLoading ? '停止回答' : ''">
        <a-button
          @click="handleSendOrStop"
          :disabled="sendButtonDisabled"
          type="link"
          class="send-button"
        >
          <template #icon>
            <component :is="getIcon" class="send-btn" />
          </template>
        </a-button>
      </a-tooltip>
    </div>

    <div class="bottom-slot">
      <slot name="bottom"></slot>
    </div>
  </div>
</template>

<script setup>
import {
  ref,
  computed,
  onMounted,
  nextTick,
  watch,
  onBeforeUnmount,
  useSlots,
  h,
  render
} from 'vue'
import { SendOutlined, ArrowUpOutlined, PauseOutlined } from '@ant-design/icons-vue'
import { Plus } from 'lucide-vue-next'
import { searchMentionFiles } from '@/apis/mention_api'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import { useOutsidePointerdown } from '@/composables/useOutsidePointerdown'
import { buildMentionResourceItems } from '@/utils/mention_resource_items'
import {
  getMentionIconComponent,
  getMentionIconStyle,
  MENTION_ICON_SIZE,
  MENTION_ICON_STROKE_WIDTH
} from '@/utils/mention_icon_utils'
import {
  buildMentionDisplayLabels,
  expandMentionDeletionRange,
  findActiveMentionQuery,
  formatMentionToken,
  getMentionDisplayLabel,
  parseMentionText,
  replaceRawRange
} from '@/utils/mention_utils'

// 点击外部关闭下拉框
const mentionDropdownRef = ref(null)
const closeMentionPopup = (e) => {
  if (!mentionPopupVisible.value) return
  if (inputRef.value?.contains(e.target)) return
  if (mentionDropdownRef.value?.contains(e.target)) return
  mentionPopupVisible.value = false
}

const inputRef = ref(null)
const optionsExpanded = ref(false)
const optionsTriggerRef = ref(null)
const optionsPanelRef = ref(null)
// 用于防抖的定时器
const debounceTimer = ref(null)
const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '输入问题...'
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  sendButtonDisabled: {
    type: Boolean,
    default: false
  },
  autoSize: {
    type: Object,
    default: () => ({ minRows: 2, maxRows: 6 })
  },
  sendIcon: {
    type: String,
    default: 'ArrowUpOutlined'
  },
  customClasses: {
    type: Object,
    default: () => ({})
  },
  mention: {
    type: Object,
    default: () => null
  },
  threadId: {
    type: String,
    default: ''
  },
  fileUploadEnabled: {
    type: Boolean,
    default: false
  },
  showOptionsLeft: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:modelValue', 'send', 'keydown', 'paste-image', 'drop-files'])
const slots = useSlots()

// @ 提及功能是否启用
const mentionEnabled = computed(() => {
  return !!props.mention
})

const mentionDisplayLabels = computed(() => buildMentionDisplayLabels(props.mention || {}))
const mentionResourceItems = computed(() => buildMentionResourceItems(props.mention || {}))

let lastRawSelectionRange = null
let lastSyncedEditorValue = props.modelValue || ''

const getStoredRawSelectionRange = () => {
  const length = getEditorRawValue().length
  if (!lastRawSelectionRange) {
    return { start: length, end: length, collapsed: true }
  }

  const start = Math.max(0, Math.min(lastRawSelectionRange.start, length))
  const end = Math.max(start, Math.min(lastRawSelectionRange.end, length))
  return { start, end, collapsed: start === end }
}

const rememberRawSelectionRange = (range) => {
  lastRawSelectionRange = { ...range }
  return range
}

const formatMentionPath = (path) => {
  if (!path) return ''
  let cleanPath = path.replace(/^\/?home\/gem\/user-data\/?/, '')
  if (cleanPath.startsWith('/')) {
    cleanPath = cleanPath.substring(1)
  }
  // 如果以 / 结尾，说明它是一个目录，我们先去掉末尾的 / 之后再算父目录
  let isDir = cleanPath.endsWith('/')
  let pathForParent = isDir ? cleanPath.substring(0, cleanPath.length - 1) : cleanPath

  const lastSlashIndex = pathForParent.lastIndexOf('/')
  if (lastSlashIndex === -1) {
    return ''
  }
  return pathForParent.substring(0, lastSlashIndex + 1)
}

const getMentionDescription = (description) => {
  const value = String(description || '').trim()
  if (!value || value === '暂无描述') return ''
  return value
}

// 高性能且安全的关键字切片高亮解析函数 (100% 防御 XSS，避开危险的 v-html)
const splitTextByQuery = (text, query) => {
  if (!text) return []
  if (!query) return [{ text, isMatch: false }]

  const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escapedQuery})`, 'gi')
  const parts = text.split(regex)

  return parts.map((part) => ({
    text: part,
    isMatch: part.toLowerCase() === query.toLowerCase()
  }))
}

const isTextNode = (node) => node?.nodeType === Node.TEXT_NODE
const isElementNode = (node) => node?.nodeType === Node.ELEMENT_NODE
const isMentionNode = (node) => isElementNode(node) && node.dataset?.mentionRaw !== undefined
const isLineBreakNode = (node) => isElementNode(node) && node.tagName === 'BR'
const childIndex = (node) => Array.prototype.indexOf.call(node.parentNode?.childNodes || [], node)

const getRawNodeLength = (node) => {
  if (!node) return 0
  if (isTextNode(node)) return node.textContent?.length || 0
  if (isMentionNode(node)) return node.dataset.mentionRaw?.length || 0
  if (isLineBreakNode(node)) return 1
  return Array.from(node.childNodes || []).reduce(
    (total, child) => total + getRawNodeLength(child),
    0
  )
}

const serializeEditorNode = (node) => {
  if (!node) return ''
  if (isTextNode(node)) return node.textContent || ''
  if (isMentionNode(node)) return node.dataset.mentionRaw || ''
  if (isLineBreakNode(node)) return '\n'
  return Array.from(node.childNodes || [])
    .map((child) => serializeEditorNode(child))
    .join('')
}

const serializeEditorContent = () => serializeEditorNode(inputRef.value)
const getEditorRawValue = () => (inputRef.value ? serializeEditorContent() : inputValue.value)

const unmountEditorMentionIcons = () => {
  inputRef.value
    ?.querySelectorAll('.mention-ref-icon[data-vue-icon]')
    .forEach((container) => render(null, container))
}

const createEditorMentionElement = (segment) => {
  const token = document.createElement('span')
  token.className = `mention-ref-token mention-ref-${segment.type} mention-ref-editable`
  token.contentEditable = 'false'
  token.dataset.mentionRaw = segment.raw
  token.dataset.mentionType = segment.type
  token.dataset.mentionValue = segment.value
  token.title = segment.raw

  const icon = document.createElement('span')
  icon.className = 'mention-ref-icon'
  icon.dataset.vueIcon = 'true'
  if (segment.type === 'file') {
    render(
      h(FileTypeIcon, {
        name: segment.value,
        isDir: segment.value.endsWith('/'),
        size: MENTION_ICON_SIZE
      }),
      icon
    )
  } else {
    Object.assign(icon.style, getMentionIconStyle(segment.type, segment.value) || {})
    render(
      h(getMentionIconComponent(segment.type, segment.value), {
        size: MENTION_ICON_SIZE,
        strokeWidth: MENTION_ICON_STROKE_WIDTH
      }),
      icon
    )
  }
  token.appendChild(icon)

  const label = document.createElement('span')
  label.className = 'mention-ref-label'
  label.textContent = getMentionDisplayLabel(
    segment.type,
    segment.value,
    mentionDisplayLabels.value
  )
  token.appendChild(label)

  return token
}

const renderEditorContent = (raw = '') => {
  const editor = inputRef.value
  if (!editor) return

  unmountEditorMentionIcons()
  editor.replaceChildren()
  parseMentionText(raw).forEach((segment) => {
    editor.appendChild(
      segment.kind === 'text'
        ? document.createTextNode(segment.text)
        : createEditorMentionElement(segment)
    )
  })
  lastSyncedEditorValue = String(raw || '')
}

const isNodeInEditor = (node) => {
  const editor = inputRef.value
  if (!editor || !node) return false
  const element = isElementNode(node) ? node : node.parentNode
  return element === editor || editor.contains(element)
}

const getRawOffsetFromDomPoint = (container, offset) => {
  const editor = inputRef.value
  if (!editor || !isNodeInEditor(container)) return getEditorRawValue().length

  let rawOffset = 0
  let found = false

  const visit = (node) => {
    if (!node || found) return

    if (node === container) {
      if (isTextNode(node)) {
        rawOffset += Math.min(offset, node.textContent?.length || 0)
      } else if (isMentionNode(node)) {
        rawOffset += offset > 0 ? getRawNodeLength(node) : 0
      } else {
        const children = Array.from(node.childNodes || [])
        for (let index = 0; index < Math.min(offset, children.length); index++) {
          rawOffset += getRawNodeLength(children[index])
        }
      }
      found = true
      return
    }

    if (isTextNode(node) || isMentionNode(node) || isLineBreakNode(node)) {
      rawOffset += getRawNodeLength(node)
      return
    }

    for (const child of Array.from(node.childNodes || [])) {
      visit(child)
      if (found) return
    }
  }

  visit(editor)
  return rawOffset
}

const getRawSelectionRange = () => {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || !isNodeInEditor(selection.anchorNode)) {
    return getStoredRawSelectionRange()
  }

  const anchor = getRawOffsetFromDomPoint(selection.anchorNode, selection.anchorOffset)
  const focus = getRawOffsetFromDomPoint(selection.focusNode, selection.focusOffset)
  return rememberRawSelectionRange({
    start: Math.min(anchor, focus),
    end: Math.max(anchor, focus),
    collapsed: anchor === focus
  })
}

const getDomPointForRawOffset = (rawOffset) => {
  const editor = inputRef.value
  const offset = Math.max(0, Math.min(rawOffset, getEditorRawValue().length))
  let remaining = offset

  const pointBeforeNode = (node) => ({ node: node.parentNode || editor, offset: childIndex(node) })
  const pointAfterNode = (node) => ({
    node: node.parentNode || editor,
    offset: childIndex(node) + 1
  })

  const visit = (node) => {
    if (!node) return null

    if (isTextNode(node)) {
      const length = node.textContent?.length || 0
      if (remaining <= length) {
        return { node, offset: remaining }
      }
      remaining -= length
      return null
    }

    if (isMentionNode(node) || isLineBreakNode(node)) {
      const length = getRawNodeLength(node)
      if (remaining === 0) return pointBeforeNode(node)
      if (remaining <= length) return pointAfterNode(node)
      remaining -= length
      return null
    }

    for (const child of Array.from(node.childNodes || [])) {
      const point = visit(child)
      if (point) return point
    }

    return node === editor ? { node: editor, offset: editor.childNodes.length } : null
  }

  return visit(editor) || { node: editor, offset: editor?.childNodes.length || 0 }
}

const restoreEditorSelection = (start, end = start) => {
  const editor = inputRef.value
  const selection = window.getSelection()
  if (!editor || !selection) return

  const startPoint = getDomPointForRawOffset(start)
  const endPoint = getDomPointForRawOffset(end)
  const range = document.createRange()
  range.setStart(startPoint.node, startPoint.offset)
  range.setEnd(endPoint.node, endPoint.offset)
  selection.removeAllRanges()
  selection.addRange(range)
  rememberRawSelectionRange({ start, end, collapsed: start === end })
  editor.focus()
}

const updateRawValue = (value, caretStart, caretEnd = caretStart) => {
  renderEditorContent(value)
  emit('update:modelValue', value)
  nextTick(() => {
    restoreEditorSelection(caretStart, caretEnd)
    adjustTextareaHeight()
    if (mentionEnabled.value) {
      checkMentionTrigger()
    }
  })
}

const replaceCurrentRawSelection = (replacement) => {
  const currentValue = getEditorRawValue()
  const range = getRawSelectionRange()
  const nextValue = replaceRawRange(currentValue, range.start, range.end, replacement)
  const nextOffset = range.start + replacement.length
  updateRawValue(nextValue, nextOffset)
}

// 检测是否在 @ 触发位置
const checkMentionTrigger = () => {
  if (!inputRef.value || !mentionEnabled.value) return false

  const selectionRange = getRawSelectionRange()
  if (!selectionRange.collapsed) {
    mentionPopupVisible.value = false
    return false
  }

  const activeMention = findActiveMentionQuery(getEditorRawValue(), selectionRange.end)
  if (activeMention) {
    const sameQuery = mentionPopupVisible.value && mentionQuery.value === activeMention.query
    mentionQuery.value = activeMention.query
    mentionPopupVisible.value = true
    if (!sameQuery) {
      mentionSelectedIndex.value = 0
      updateMentionItems(mentionQuery.value)
    }
    return true
  }

  mentionPopupVisible.value = false
  return false
}

// 更新提及候选项
const updateMentionItems = (query = '') => {
  const normalizedQuery = String(query || '')
  if (!normalizedQuery) {
    clearTimeout(mentionSearchTimer)
    if (activeAbortController) {
      activeAbortController.abort()
      activeAbortController = null
    }
    searchRequestId.value++
  }

  if (!props.mention) {
    mentionItems.value = { files: [], knowledgeBases: [], mcps: [], skills: [], subagents: [] }
    return
  }

  const lowerQuery = normalizedQuery.toLowerCase()
  const { files = [] } = props.mention

  const filterItems = (list) =>
    list.filter((item) => {
      const searchTexts = [
        item.label,
        item.value,
        item.description,
        item.resourceId,
        item.tokenLabel,
        item.type
      ]
      return searchTexts.some((text) =>
        String(text || '')
          .toLowerCase()
          .includes(lowerQuery)
      )
    })

  // 本地临时文件/附件候选项过滤
  const localFileItems = files.map((f) => {
    const path = f.path || ''
    const fileName = path.split('/').pop() || path
    return {
      value: path,
      label: fileName,
      type: 'file',
      insertValue: path || fileName,
      tokenLabel: formatMentionToken('file', fileName),
      description: path
    }
  })

  const filteredLocalFiles = normalizedQuery ? filterItems(localFileItems) : []

  const {
    knowledgeBases: knowledgeItems,
    mcps: mcpItems,
    skills: skillItems,
    subagents: subagentItems
  } = mentionResourceItems.value

  // 初始化设置 mentionItems 状态（使用前端已有的本地过滤结果，瞬间更新，达到零卡顿）
  mentionItems.value = {
    files: filteredLocalFiles,
    knowledgeBases: filterItems(knowledgeItems),
    mcps: filterItems(mcpItems),
    skills: filterItems(skillItems),
    subagents: filterItems(subagentItems)
  }

  if (normalizedQuery) {
    const activeThreadId = props.threadId || ''
    clearTimeout(mentionSearchTimer)
    if (activeAbortController) {
      activeAbortController.abort()
      activeAbortController = null
    }
    searchRequestId.value++
    const currentId = searchRequestId.value

    mentionSearchTimer = setTimeout(async () => {
      activeAbortController = new AbortController()

      try {
        const responseData = await searchMentionFiles(
          activeThreadId,
          normalizedQuery,
          activeAbortController.signal
        )

        // 竞态校验锁，确保是当前最新响应
        if (currentId === searchRequestId.value && Array.isArray(responseData)) {
          const remoteFileItems = responseData.map((f) => {
            const path = f.path || ''
            const fileName = f.name || path.split('/').pop() || path
            return {
              value: path,
              label: fileName,
              type: 'file',
              insertValue: path || fileName,
              tokenLabel: formatMentionToken('file', fileName),
              description: path,
              is_dir: f.is_dir,
              source: f.source
            }
          })

          // 合并本地临时文件与后端高匹配度文件（使用 Set 进行去重，防止重复展示）
          const seenValues = new Set(filteredLocalFiles.map((x) => x.value))
          const mergedFiles = [...filteredLocalFiles]

          remoteFileItems.forEach((item) => {
            if (!seenValues.has(item.value)) {
              seenValues.add(item.value)
              mergedFiles.push(item)
            }
          })

          mentionItems.value.files = mergedFiles
        }
      } catch (error) {
        // 主动取消的请求我们不作为错误抛出
        if (error.name !== 'AbortError') {
          console.error('Mention search error:', error)
        }
      } finally {
        if (currentId === searchRequestId.value) {
          activeAbortController = null
        }
      }
    }, 250) // 250ms 经典防抖时间
  }
}

// 检查项是否被选中
const isItemSelected = (type, index) => {
  if (mentionSelectedIndex.value < 0) return false

  const filesLen = mentionItems.value.files.length
  const kbLen = mentionItems.value.knowledgeBases.length
  const mcpLen = mentionItems.value.mcps.length
  const skillsLen = mentionItems.value.skills.length

  if (type === 'file') {
    return mentionSelectedIndex.value === index
  } else if (type === 'knowledge') {
    return mentionSelectedIndex.value === filesLen + index
  } else if (type === 'mcp') {
    return mentionSelectedIndex.value === filesLen + kbLen + index
  } else if (type === 'skill') {
    return mentionSelectedIndex.value === filesLen + kbLen + mcpLen + index
  } else {
    return mentionSelectedIndex.value === filesLen + kbLen + mcpLen + skillsLen + index
  }
}

// 是否有任何候选项
const showFileSearchPrompt = computed(() => {
  return Boolean(props.mention?.files?.length) && !mentionQuery.value
})

const hasAnyItems = computed(() => {
  const items = mentionItems.value
  return (
    showFileSearchPrompt.value ||
    items.files.length > 0 ||
    items.knowledgeBases.length > 0 ||
    items.mcps.length > 0 ||
    items.skills.length > 0 ||
    items.subagents.length > 0
  )
})

const commitMention = (item, range = null) => {
  if (!inputRef.value || props.disabled) return
  const mentionValue = item?.insertValue || item?.value
  if (!item?.type || !mentionValue) return

  const mentionText = `${formatMentionToken(item.type, mentionValue)} `
  mentionPopupVisible.value = false
  mentionQuery.value = ''

  if (range) {
    const currentValue = getEditorRawValue()
    const newValue = replaceRawRange(currentValue, range.start, range.end, mentionText)
    updateRawValue(newValue, range.start + mentionText.length)
    return
  }
  replaceCurrentRawSelection(mentionText)
}

const insertMention = (item) => {
  if (!inputRef.value) return

  const currentValue = getEditorRawValue()
  const selectionRange = getRawSelectionRange()
  const activeMention = findActiveMentionQuery(currentValue, selectionRange.end)
  if (!activeMention) return

  commitMention(item, { start: activeMention.start, end: activeMention.end })
}

// 滚动到选中项
const scrollToItem = (index) => {
  nextTick(() => {
    const popup = mentionDropdownRef.value?.querySelector('.mention-popup')
    if (!popup) return

    // 查找所有 mention-item 元素
    const items = popup.querySelectorAll('.mention-item')
    const selectedItem = items[index]

    if (selectedItem) {
      // 检查元素是否在可视区域内
      const popupRect = popup.getBoundingClientRect()
      const itemRect = selectedItem.getBoundingClientRect()

      if (itemRect.bottom > popupRect.bottom) {
        selectedItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      } else if (itemRect.top < popupRect.top) {
        selectedItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }
    }
  })
}

// 处理键盘导航
const handleMentionNavigation = (e) => {
  if (!mentionPopupVisible.value) return

  const allItems = [
    ...mentionItems.value.files,
    ...mentionItems.value.knowledgeBases,
    ...mentionItems.value.mcps,
    ...mentionItems.value.skills,
    ...mentionItems.value.subagents
  ]

  const total = allItems.length
  if (total === 0) return

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    mentionSelectedIndex.value = (mentionSelectedIndex.value + 1) % total
    scrollToItem(mentionSelectedIndex.value)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    mentionSelectedIndex.value = (mentionSelectedIndex.value - 1 + total) % total
    scrollToItem(mentionSelectedIndex.value)
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    if (mentionSelectedIndex.value >= 0 && mentionSelectedIndex.value < total) {
      e.preventDefault()
      insertMention(allItems[mentionSelectedIndex.value])
    }
  } else if (e.key === 'Escape') {
    e.preventDefault()
    mentionPopupVisible.value = false
  }
}

const hasOptionsLeft = computed(() => {
  if (!props.showOptionsLeft) {
    return false
  }
  const slot = slots['options-left']
  if (!slot) {
    return false
  }
  const renderedNodes = slot()
  return Boolean(renderedNodes && renderedNodes.length)
})

// 图标映射
const iconComponents = {
  SendOutlined: SendOutlined,
  ArrowUpOutlined: ArrowUpOutlined,
  PauseOutlined: PauseOutlined
}

// 根据传入的图标名动态获取组件
const getIcon = computed(() => {
  if (props.isLoading) {
    return PauseOutlined
  }
  return iconComponents[props.sendIcon] || ArrowUpOutlined
})

// 创建本地引用以进行双向绑定
const inputValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const isDraggingFiles = ref(false)

const hasTransferFiles = (dataTransfer) =>
  Array.from(dataTransfer?.types || []).some((type) => type === 'Files')

const canAcceptUploadFiles = () => props.fileUploadEnabled && !props.disabled && !props.isLoading

const getImageFileFromClipboard = (clipboardData) => {
  const items = Array.from(clipboardData?.items || [])
  for (const item of items) {
    if (item.kind === 'file' && item.type?.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) return file
    }
  }

  return Array.from(clipboardData?.files || []).find((file) => file.type?.startsWith('image/'))
}

const handleMentionDeletion = (e) => {
  if (e.key !== 'Backspace' && e.key !== 'Delete') return false

  const currentValue = getEditorRawValue()
  const selectionRange = getRawSelectionRange()
  const expandedRange = expandMentionDeletionRange(
    currentValue,
    selectionRange.start,
    selectionRange.end,
    e.key === 'Delete' ? 'forward' : 'backward'
  )

  if (!expandedRange) return false

  e.preventDefault()
  const nextValue = replaceRawRange(currentValue, expandedRange.start, expandedRange.end, '')
  mentionPopupVisible.value = false
  updateRawValue(nextValue, expandedRange.start)
  return true
}

// 处理键盘事件
const handleKeyPress = (e) => {
  // @ 提及键盘导航
  if (mentionPopupVisible.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)) {
      handleMentionNavigation(e)
      return
    }
  }

  if (handleMentionDeletion(e)) {
    return
  }

  if (e.key === 'Enter' && e.shiftKey) {
    e.preventDefault()
    replaceCurrentRawSelection('\n')
    return
  }

  emit('keydown', e)
}

const shouldCheckMentionOnKeyUp = (e) => {
  if (!e) return false
  if (e.key.length === 1) return true
  return e.key === 'Backspace' || e.key === 'Delete'
}

// 检测 @ 触发
const handleKeyUp = (e) => {
  if (!mentionEnabled.value || isComposing.value || !shouldCheckMentionOnKeyUp(e)) return
  nextTick(() => {
    checkMentionTrigger()
  })
}

// 处理输入事件
const handleInput = () => {
  if (isComposing.value) return

  if (inputRef.value && !inputRef.value.querySelector('.mention-ref-token')) {
    const text = inputRef.value.textContent || ''
    if (!text.trim()) {
      inputRef.value.replaceChildren()
    }
  }

  const value = serializeEditorContent()
  lastSyncedEditorValue = value
  emit('update:modelValue', value)
  adjustTextareaHeight()

  if (mentionEnabled.value) {
    nextTick(() => {
      checkMentionTrigger()
    })
  }
}

const handlePaste = (e) => {
  if (props.disabled) return

  const imageFile = canAcceptUploadFiles() ? getImageFileFromClipboard(e.clipboardData) : null
  if (imageFile) {
    e.preventDefault()
    emit('paste-image', imageFile)
    return
  }

  e.preventDefault()
  const text = e.clipboardData?.getData('text/plain') || ''
  if (!text) return
  replaceCurrentRawSelection(text)
}

const handleDragEnter = (e) => {
  if (!hasTransferFiles(e.dataTransfer)) return
  e.preventDefault()
  if (!canAcceptUploadFiles()) return
  isDraggingFiles.value = true
}

const handleDragOver = (e) => {
  if (!hasTransferFiles(e.dataTransfer)) return
  e.preventDefault()
  e.dataTransfer.dropEffect = canAcceptUploadFiles() ? 'copy' : 'none'
  if (!canAcceptUploadFiles()) return
  isDraggingFiles.value = true
}

const handleDragLeave = (e) => {
  if (e.currentTarget?.contains(e.relatedTarget)) return
  isDraggingFiles.value = false
}

const handleDrop = (e) => {
  if (!hasTransferFiles(e.dataTransfer)) return
  e.preventDefault()
  e.stopPropagation()
  isDraggingFiles.value = false
  if (!canAcceptUploadFiles()) return
  const files = Array.from(e.dataTransfer?.files || [])
  if (!files.length) return

  emit('drop-files', files)
}

const handleCompositionStart = () => {
  isComposing.value = true
}

const handleCompositionEnd = () => {
  isComposing.value = false
  handleInput()
}

// 处理发送按钮点击
const handleSendOrStop = () => {
  emit('send')
}

// @ 提及功能状态
const mentionPopupVisible = ref(false)
const mentionQuery = ref('')
const mentionItems = ref({ files: [], knowledgeBases: [], mcps: [], skills: [], subagents: [] })
const mentionSelectedIndex = ref(0)
const searchRequestId = ref(0)
const isComposing = ref(false)
let activeAbortController = null
let mentionSearchTimer = null

watch(optionsExpanded, (open) => {
  if (open) {
    mentionPopupVisible.value = false
  }
})

useOutsidePointerdown(optionsExpanded, [optionsTriggerRef, optionsPanelRef])

const adjustTextareaHeight = () => {
  if (!inputRef.value) {
    return
  }

  const textarea = inputRef.value
  textarea.style.height = 'auto'
  textarea.style.height = `${textarea.scrollHeight}px`
}

// 聚焦输入框
const focusInput = () => {
  if (inputRef.value && !props.disabled) {
    inputRef.value.focus()
    // 聚焦回来时，如果开启了提及，自动检测当前光标位置是否处于 @提及 范围，是则重新升起弹框
    if (mentionEnabled.value) {
      nextTick(() => {
        checkMentionTrigger()
      })
    }
  }
}

const handleInputBoxClick = (event) => {
  if (event.target instanceof Element) {
    const interactiveTarget = event.target.closest(
      'button, a, input, textarea, select, [role="button"], [role="menuitem"], [role="switch"]'
    )
    if (interactiveTarget) return
  }
  focusInput()
}

// 处理输入框点击事件，自适应检测光标是否落入 @提及 范围内以唤醒或更新弹窗
const handleEditorClick = () => {
  if (mentionEnabled.value) {
    nextTick(() => {
      checkMentionTrigger()
    })
  }
}

// 监听输入值变化
watch(inputValue, (value) => {
  if (value !== lastSyncedEditorValue) {
    renderEditorContent(value || '')
  }

  if (debounceTimer.value) {
    clearTimeout(debounceTimer.value)
  }
  debounceTimer.value = setTimeout(() => {
    nextTick(() => {
      adjustTextareaHeight()
    })
  }, 100)
})

onMounted(() => {
  document.addEventListener('click', closeMentionPopup)
  nextTick(() => {
    if (inputRef.value) {
      renderEditorContent(inputValue.value || '')
      adjustTextareaHeight()
      inputRef.value.focus()
    }
  })
})

// 组件卸载时清除定时器和事件监听器
onBeforeUnmount(() => {
  unmountEditorMentionIcons()
  if (debounceTimer.value) {
    clearTimeout(debounceTimer.value)
  }
  if (mentionSearchTimer) {
    clearTimeout(mentionSearchTimer)
  }
  if (activeAbortController) {
    activeAbortController.abort()
  }
  isDraggingFiles.value = false
  document.removeEventListener('click', closeMentionPopup)
})

// 公开方法供父组件调用
defineExpose({
  focus: () => inputRef.value?.focus(),
  insertMention: commitMention,
  closeOptions: () => {
    optionsExpanded.value = false
  }
})
</script>

<style lang="less" scoped>
.input-box {
  container-type: inline-size;
  display: grid;
  width: 100%;
  margin: 0 auto;
  border: 1px solid var(--gray-150);
  border-radius: 0.8rem;
  box-shadow: 0 2px 8px var(--shadow-1);
  transition: all 0.3s ease;
  background: var(--gray-0);
  gap: 0px;
  position: relative;

  /* Default: Multi-line layout with top/bottom slots */
  padding: 0.8rem 0.75rem 0.6rem 0.75rem;
  grid-template-columns: auto 1fr;
  grid-template-rows: auto auto auto;
  grid-template-areas:
    'top top'
    'input input'
    'options send';

  .top-slot {
    display: flex;
    grid-area: top;
  }

  .expand-options {
    grid-area: options;
    justify-self: start;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .user-input {
    grid-area: input;
  }

  .send-button-container {
    grid-area: send;
    justify-self: end;
  }

  .bottom-slot {
    grid-column: 1 / -1;
  }

  // &:focus-within {
  //   border-color: var(--main-500);
  //   background: var(--gray-0);
  //   box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  // }

  &.dragging-files {
    border-color: var(--main-color);
    background: var(--main-10);

    &::after {
      content: '释放以上传附件';
      position: absolute;
      inset: 6px;
      z-index: 5;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px dashed var(--main-color);
      border-radius: 10px;
      color: var(--main-700);
      background: color-mix(in srgb, var(--gray-0) 88%, transparent);
      font-size: 13px;
      font-weight: 600;
      pointer-events: none;
    }
  }
}

.expand-options {
  grid-area: options;
  display: flex;
  align-items: center;
}

.user-input {
  grid-area: input;
  width: 100%;
  padding: 0;
  background-color: transparent;
  border: none;
  margin: 0;
  margin-bottom: 0.5rem;
  color: var(--gray-1000);
  font-size: 15px;
  outline: none;
  resize: none;
  line-height: 1.5;
  font-family: inherit;
  min-height: 44px; /* Default min-height for multi-line */
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: text;

  &:focus {
    outline: none;
    box-shadow: none;
  }

  &::placeholder {
    color: var(--gray-400);
  }

  &.mention-editor {
    position: relative;

    &:empty::before {
      content: attr(data-placeholder);
      position: absolute;
      left: 0;
      top: 0;
      color: var(--gray-400);
      pointer-events: none;
    }
  }

  &[contenteditable='false'] {
    cursor: not-allowed;
  }

  :deep(.mention-ref-token) {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    max-width: min(100%, 360px);
    color: var(--main-700);
    line-height: normal;
    vertical-align: baseline;
    white-space: nowrap;
    user-select: all;
  }

  :deep(.mention-ref-icon) {
    position: relative;
    top: 2px;
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    font-size: 13px;
    line-height: 1;
    margin-left: 4px;
  }

  :deep(.mention-ref-icon svg) {
    display: block;
  }

  :deep(.mention-ref-label) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: normal;
    font-weight: 500;
  }
}

.send-button-container {
  grid-area: send;
  display: flex;
  align-items: center;
  justify-content: center;
}

.expand-btn {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--gray-600);
  padding: 0;
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
  border: 1px solid transparent;
  background-color: transparent;

  &:hover {
    color: var(--gray-900);
    background: var(--gray-50);
  }

  &.active {
    color: var(--gray-900);
    background: var(--gray-100);
  }
}

// Popover 选项样式
.popover-options {
  min-width: 160px;
  max-width: 200px;
  padding: 4px;

  .no-options {
    color: var(--gray-500);
    font-size: 12px;
    text-align: center;
    padding: 12px 8px;
  }

  :deep(.opt-item) {
    border-radius: 8px;
    padding: 6px 10px;
    cursor: pointer;
    font-size: 12px;
    color: var(--gray-700);
    transition: all 0.2s ease;
    margin: 2px;
    display: inline-block;

    &:hover {
      background-color: var(--main-10);
      color: var(--main-600);
    }

    &.active {
      color: var(--main-600);
      background-color: var(--main-10);
    }
  }
}

.send-button.ant-btn-icon-only {
  height: 32px;
  width: 32px;
  cursor: pointer;
  background-color: var(--main-500);
  border-radius: 50%;
  border: none;
  transition: all 0.2s ease;
  box-shadow: 0 2px 6px var(--shadow-2);
  color: var(--gray-0);
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;

  &:hover {
    background-color: var(--main-color);
    box-shadow: 0 4px 8px var(--shadow-3);
    color: var(--gray-0);
  }

  &:active {
    box-shadow: 0 2px 4px var(--shadow-2);
    // 移除点击动画效果
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
  }
}

@media (max-width: 520px) {
  .input-box {
    border-radius: 15px;
    padding: 0.625rem 0.875rem;
  }
}

// @ 提及弹窗样式
.mention-dropdown-wrapper {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin-bottom: 8px;
  z-index: 1000;
}

.mention-popup {
  width: 100%;
  max-height: 280px;
  overflow-y: auto;
  background: var(--gray-0);
  border-radius: 8px;
  box-shadow:
    0 -4px 16px rgba(0, 0, 0, 0.08),
    0 4px 16px rgba(0, 0, 0, 0.12);
  border: 1px solid var(--gray-200);
  padding: 8px 0;

  .mention-group {
    margin-bottom: 4px;

    &:last-child {
      margin-bottom: 0;
    }
  }

  .mention-group-title {
    font-size: 12px;
    color: var(--gray-500);
    padding: 4px 8px;
    display: flex;
    align-items: center;
    gap: 4px;
    border-bottom: 1px solid var(--gray-100);
    margin-bottom: 2px;
  }

  .mention-item {
    padding: 4px 8px;
    cursor: pointer;
    font-size: 13px;
    color: var(--gray-700);
    transition: all 0.15s ease;
    margin: 1px 4px;
    border-radius: 4px;

    &.resource-item {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding: 6px 10px;

      .resource-name {
        color: var(--gray-800);
        font-weight: 500;
        flex: 0 0 auto;
        white-space: nowrap;
      }

      .resource-description {
        color: var(--gray-500);
        font-size: 12px;
        line-height: 1.35;
        flex: 1 1 auto;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }

    &.file-item {
      display: flex;
      flex-direction: row;
      align-items: center;
      justify-content: flex-start;
      gap: 0;
      padding: 6px 10px;

      .file-info-left {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 1;
        min-width: 0;

        .file-type-icon {
          font-size: 15px;
          flex-shrink: 0;
          display: flex;
          align-items: center;
        }

        .file-name {
          font-weight: 500;
          color: var(--gray-800);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 13px;
        }
      }

      .file-parent-dir {
        font-size: 11px;
        color: var(--gray-400);
        margin-left: 8px;
        flex-shrink: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        transition: color 0.15s ease;
      }
    }

    &:hover {
      background-color: var(--main-10);
      color: var(--main-600);

      &.resource-item {
        .resource-name {
          color: var(--main-600);
        }
        .resource-description {
          color: var(--main-400);
        }
      }

      &.file-item {
        .file-info-left .file-name {
          color: var(--main-600);
        }
        .file-parent-dir {
          color: var(--main-400);
        }
      }
    }

    &.active {
      background-color: var(--gray-50);
      color: var(--main-600);

      &.resource-item {
        .resource-name {
          color: var(--main-600);
        }
        .resource-description {
          color: var(--main-400);
        }
      }

      &.file-item {
        .file-info-left .file-name {
          color: var(--main-600);
        }
        .file-parent-dir {
          color: var(--main-400);
        }
      }
    }
  }

  .query-match {
    color: #fa8c16; /* 明亮温润的金橘色 */
    font-weight: 700;
  }

  .mention-empty {
    text-align: center;
    padding: 12px 8px;
    color: var(--gray-400);
    font-size: 13px;
  }

  .mention-search-placeholder {
    padding: 4px 8px;
    color: var(--gray-400);
    font-size: 13px;
  }
}
</style>
