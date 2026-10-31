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
      <ActionTrigger
        label="添加内容"
        :open="mentionPopupVisible"
        :disabled="disabled"
        icon-only
        @mousedown.prevent
        @click.stop="toggleAddMenu"
      >
        <template #icon><Plus :size="17" /></template>
      </ActionTrigger>
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
        <div v-if="addActions.length" class="mention-group">
          <div class="mention-group-title">添加</div>
          <button
            v-for="(item, index) in addActions"
            :key="item.value"
            type="button"
            :class="['mention-item', 'resource-item', 'add-action', { active: mentionSelectedIndex === index }]"
            @click="selectAddAction(item)"
          >
            <component :is="item.icon" :size="15" aria-hidden="true" />
            <span>{{ item.label }}</span>
          </button>
        </div>
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
            <MentionResourceAvatar :item="item" />
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
            <MentionResourceAvatar :item="item" />
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
            <MentionResourceAvatar :item="item" />
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

        <!-- 智能体列表 -->
        <div v-if="mentionItems.agents.length > 0" class="mention-group">
          <div class="mention-group-title">智能体</div>
          <div
            v-for="(item, index) in mentionItems.agents"
            :key="'agent-' + item.value"
            :class="['mention-item', 'resource-item', { active: isItemSelected('agent', index) }]"
            @click="insertMention(item)"
          >
            <MentionResourceAvatar :item="item" />
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

        <!-- 对话列表 -->
        <div class="mention-group session-mention-group">
          <div class="mention-group-title">
            对话 <span class="session-search-hint">{{ mentionQuery ? '仅搜索标题' : '最近对话 · 输入标题搜索' }}</span>
          </div>
          <div v-if="sessionSearchLoading" class="mention-search-placeholder" role="status">正在查找对话…</div>
          <div v-else-if="sessionSearchError" class="session-search-error" role="status">
            对话加载失败
            <button type="button" @click.stop="updateMentionItems(mentionQuery)">重试</button>
          </div>
          <div v-else-if="!mentionItems.sessions.length" class="mention-search-placeholder">
            {{ mentionQuery ? '未找到标题匹配的对话，试试其他关键词' : '暂无可引用的对话' }}
          </div>
          <button
            v-for="(item, index) in mentionItems.sessions"
            :key="item.value"
            type="button"
            :class="['mention-item', 'session-mention-item', { active: isItemSelected('session', index) }]"
            :title="item.label"
            @click="insertMention(item)"
          >
            <MessageCircle :size="16" aria-hidden="true" />
            <span class="session-mention-title">
              <span
                v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
                :key="pIdx"
                :class="{ 'query-match': part.isMatch }"
              >{{ part.text }}</span>
            </span>
            <time v-if="item.updatedAt" class="session-mention-date">{{ item.updatedAt }}</time>
          </button>
        </div>
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
          :aria-label="isLoading ? '停止回答' : '发送消息'"
        >
          <template #icon>
            <Square v-if="isLoading" :size="14" fill="currentColor" :stroke-width="0" aria-hidden="true" />
            <component v-else :is="getIcon" class="send-btn" />
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
  h,
  render,
  getCurrentInstance
} from 'vue'
import {
  ArrowUp as ArrowUpOutlined,
  Square,
  Plus,
  Paperclip,
  ImagePlus,
  MessageCircle,
  Send as SendOutlined
} from '@lucide/vue'
import { searchMentionFiles, searchMentionSessions } from '@/apis/mention_api'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import ActionTrigger from '@/shared/ui/ActionTrigger.vue'
import MentionToken from '@/modules/session/ui/MentionToken.vue'
import MentionResourceAvatar from '@/modules/session/ui/MentionResourceAvatar.vue'
import { buildMentionResourceItems } from '@/modules/session/model/mention_resource_items'
import {
  buildMentionDisplayLabels,
  expandMentionDeletionRange,
  findActiveMentionQuery,
  formatMentionToken,
  getMentionDisplayLabel,
  parseMentionText,
  replaceRawRange
} from '@/modules/session/model/mention_utils'

// 点击外部关闭下拉框
const mentionDropdownRef = ref(null)
const closeMentionPopup = (e) => {
  if (!mentionPopupVisible.value) return
  if (inputRef.value?.contains(e.target)) return
  if (mentionDropdownRef.value?.contains(e.target)) return
  mentionPopupVisible.value = false
}

const appContext = getCurrentInstance().appContext
const inputRef = ref(null)
const addMenuRange = ref(null)
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
  enableUploads: { type: Boolean, default: false },
  showOptionsLeft: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:modelValue', 'send', 'keydown', 'paste-images', 'drop-files', 'upload-attachment', 'add-images'])

// @ 提及功能是否启用
const mentionEnabled = computed(() => {
  return !!props.mention || props.enableUploads
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
  let cleanPath = path.replace(/^\/?home\/user\/workspace(?:\/|$)/, '')
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

const getRawOffsetBeforeNode = (node) => {
  const editor = inputRef.value
  if (!editor || !node || !isNodeInEditor(node)) return null

  let rawOffset = 0
  let current = node
  while (current && current !== editor) {
    let sibling = current.previousSibling
    while (sibling) {
      rawOffset += getRawNodeLength(sibling)
      sibling = sibling.previousSibling
    }
    current = current.parentNode
  }

  return current === editor ? rawOffset : null
}

const getMentionNodeImmediatelyBeforeCaret = () => {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || !selection.isCollapsed) return null
  if (!isNodeInEditor(selection.anchorNode)) return null

  let current = selection.anchorNode
  let offset = selection.anchorOffset
  while (current && current !== inputRef.value) {
    if (isTextNode(current)) {
      if (offset !== 0) return null
    } else if (offset > 0) {
      const candidate = current.childNodes[offset - 1]
      if (isMentionNode(candidate)) return candidate
      return null
    }

    const parent = current.parentNode
    if (!parent) return null
    offset = childIndex(current)
    current = parent
  }

  if (current === inputRef.value && offset > 0) {
    const candidate = current.childNodes[offset - 1]
    return isMentionNode(candidate) ? candidate : null
  }
  return null
}

const getDomMentionDeletionRange = () => {
  const mentionNode = getMentionNodeImmediatelyBeforeCaret()
  if (!mentionNode) return null

  const start = getRawOffsetBeforeNode(mentionNode)
  if (start === null) return null
  return { start, end: start + getRawNodeLength(mentionNode) }
}

const editorMentions = new Set()

const unmountEditorMentions = () => {
  editorMentions.forEach((container) => render(null, container))
  editorMentions.clear()
}

/** 原生剪切或替换会先移除 DOM，仍须卸载其 Vue 实例。 */
const cleanupDetachedEditorMentions = () => {
  editorMentions.forEach((container) => {
    if (inputRef.value?.contains(container)) return
    render(null, container)
    editorMentions.delete(container)
  })
}

/** 编辑器只拥有原始 token 与光标边界，外观交给共用组件。 */
const renderEditorMention = (container) => {
  const { mentionType: type, mentionValue: value } = container.dataset
  const vnode = h(MentionToken, {
    type,
    value,
    class: 'mention-ref-editable',
    label: getMentionDisplayLabel(type, value, mentionDisplayLabels.value),
    agent: props.mention?.agents?.find((agent) => agent.agent_id === value)
  })
  vnode.appContext = appContext
  render(vnode, container)
}

const createEditorMentionElement = (segment) => {
  const token = document.createElement('span')
  token.className = 'mention-ref-editable'
  token.contentEditable = 'false'
  token.dataset.mentionRaw = segment.raw
  token.dataset.mentionType = segment.type
  token.dataset.mentionValue = segment.value
  token.dataset.vueMention = 'true'
  token.title = segment.type === 'session'
    ? getMentionDisplayLabel(segment.type, segment.value, mentionDisplayLabels.value)
    : segment.raw
  renderEditorMention(token)
  editorMentions.add(token)
  return token
}

const renderEditorContent = (raw = '') => {
  const editor = inputRef.value
  if (!editor) return

  unmountEditorMentions()
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
  addMenuRange.value = null

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
  clearTimeout(mentionSearchTimer)
  activeAbortController?.abort()
  activeAbortController = null
  const currentId = ++searchRequestId.value
  sessionSearchLoading.value = true
  sessionSearchError.value = false

  const lowerQuery = normalizedQuery.toLowerCase()
  const { files = [] } = props.mention || {}

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
    agents: agentItems
  } = mentionResourceItems.value

  // 初始化设置 mentionItems 状态（使用前端已有的本地过滤结果，瞬间更新，达到零卡顿）
  mentionItems.value = {
    sessions: [],
    files: filteredLocalFiles,
    knowledgeBases: filterItems(knowledgeItems),
    mcps: filterItems(mcpItems),
    skills: filterItems(skillItems),
    agents: filterItems(agentItems).slice(0, 5)
  }

  const activeThreadId = props.threadId || ''
  mentionSearchTimer = setTimeout(async () => {
    activeAbortController = new AbortController()
    const signal = activeAbortController.signal
    const sessionSearch = searchMentionSessions(normalizedQuery, signal)
      .then((sessions) => {
        if (currentId !== searchRequestId.value) return
        const selected = mentionSelectableItems.value[mentionSelectedIndex.value]
        mentionItems.value.sessions = sessions
          .filter((session) => session.thread_id !== activeThreadId)
          .slice(0, 5)
          .map((session) => ({
            type: 'session',
            value: session.thread_id,
            label: session.title || '未命名对话',
            insertValue: `${session.thread_id} ${session.title || '未命名对话'}`,
            updatedAt: session.updated_at
              ? new Date(session.updated_at).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
              : ''
          }))
        const selectedIndex = mentionSelectableItems.value.indexOf(selected)
        if (selectedIndex >= 0) mentionSelectedIndex.value = selectedIndex
      })
      .catch((error) => {
        if (currentId === searchRequestId.value && error.name !== 'AbortError') {
          sessionSearchError.value = true
        }
      })
      .finally(() => {
        if (currentId === searchRequestId.value) sessionSearchLoading.value = false
      })

    const fileSearch = normalizedQuery
      ? searchMentionFiles(activeThreadId, normalizedQuery, signal).then((files) => {
          if (currentId !== searchRequestId.value) return
          const selected = mentionSelectableItems.value[mentionSelectedIndex.value]
          const seenValues = new Set(filteredLocalFiles.map((item) => item.value))
          for (const file of files) {
            const path = file.path || ''
            if (seenValues.has(path)) continue
            seenValues.add(path)
            mentionItems.value.files.push({
              value: path,
              label: file.name || path.split('/').pop() || path,
              type: 'file',
              insertValue: path,
              description: path,
              is_dir: file.is_dir,
              source: file.source
            })
          }
          const selectedIndex = mentionSelectableItems.value.indexOf(selected)
          if (selectedIndex >= 0) mentionSelectedIndex.value = selectedIndex
        })
      : Promise.resolve()
    await Promise.allSettled([sessionSearch, fileSearch])
    if (currentId === searchRequestId.value) activeAbortController = null
  }, 250)
}

// 检查项是否被选中
const isItemSelected = (type, index) => {
  const selectedIndex = mentionSelectedIndex.value - addActions.value.length
  if (selectedIndex < 0) return false

  const filesLen = mentionItems.value.files.length
  const kbLen = mentionItems.value.knowledgeBases.length
  const mcpLen = mentionItems.value.mcps.length
  const skillsLen = mentionItems.value.skills.length
  const agentsLen = mentionItems.value.agents.length

  if (type === 'file') {
    return selectedIndex === index
  } else if (type === 'knowledge') {
    return selectedIndex === filesLen + index
  } else if (type === 'mcp') {
    return selectedIndex === filesLen + kbLen + index
  } else if (type === 'skill') {
    return selectedIndex === filesLen + kbLen + mcpLen + index
  } else if (type === 'agent') {
    return selectedIndex === filesLen + kbLen + mcpLen + skillsLen + index
  } else if (type === 'session') {
    return selectedIndex === filesLen + kbLen + mcpLen + skillsLen + agentsLen + index
  }
  return false
}

// 是否有任何候选项
const showFileSearchPrompt = computed(() => {
  return Boolean(props.mention?.files?.length) && !mentionQuery.value
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
  if (addMenuRange.value) {
    const range = addMenuRange.value
    addMenuRange.value = null
    commitMention(item, range)
  } else if (activeMention) {
    commitMention(item, { start: activeMention.start, end: activeMention.end })
  }
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

const mentionSelectableItems = computed(() => [
  ...addActions.value,
  ...mentionItems.value.files,
  ...mentionItems.value.knowledgeBases,
  ...mentionItems.value.mcps,
  ...mentionItems.value.skills,
  ...mentionItems.value.agents,
  ...mentionItems.value.sessions
])

// 处理键盘导航
const handleMentionNavigation = (e) => {
  if (!mentionPopupVisible.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    mentionPopupVisible.value = false
    return
  }

  const allItems = mentionSelectableItems.value

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
      const item = allItems[mentionSelectedIndex.value]
      if (item.type === 'action') selectAddAction(item)
      else insertMention(item)
    }
  }
}

const hasOptionsLeft = computed(() => props.showOptionsLeft && mentionEnabled.value)
const addActions = computed(() => {
  if (!props.enableUploads || props.disabled) return []
  return [
    { type: 'action', value: 'attachment', label: '上传附件', icon: Paperclip },
    { type: 'action', value: 'images', label: '添加图片', icon: ImagePlus }
  ].filter(item => !mentionQuery.value || item.label.includes(mentionQuery.value))
})

/** 加号只记录插入位置，不向草稿写入临时触发字符。 */
const toggleAddMenu = () => {
  if (props.disabled) return
  if (mentionPopupVisible.value) {
    mentionPopupVisible.value = false
    return
  }
  addMenuRange.value = getRawSelectionRange()
  mentionQuery.value = ''
  mentionSelectedIndex.value = 0
  updateMentionItems()
  mentionPopupVisible.value = true
  inputRef.value?.focus()
}

/** 上传动作只消费当前查询片段，正文继续留在原草稿中。 */
const selectAddAction = (item) => {
  if (props.disabled) return
  if (!addMenuRange.value) {
    const range = getRawSelectionRange()
    const active = findActiveMentionQuery(getEditorRawValue(), range.end)
    if (active) {
      const value = replaceRawRange(getEditorRawValue(), active.start, active.end, '')
      updateRawValue(value, active.start)
    }
  }
  mentionPopupVisible.value = false
  mentionQuery.value = ''
  addMenuRange.value = null
  emit(item.value === 'attachment' ? 'upload-attachment' : 'add-images')
}

// 图标映射
const iconComponents = {
  SendOutlined: SendOutlined,
  ArrowUpOutlined: ArrowUpOutlined
}

// 根据传入的图标名动态获取组件
const getIcon = computed(() => {
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

const canAcceptUploadFiles = () => !props.disabled && !props.isLoading

/** 剪贴板里的全部图片：一次粘多张时不能只留第一张。 */
const getImageFilesFromClipboard = (clipboardData) => {
  const files = []
  for (const item of Array.from(clipboardData?.items || [])) {
    if (item.kind === 'file' && item.type?.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) files.push(file)
    }
  }

  for (const file of Array.from(clipboardData?.files || [])) {
    if (file.type?.startsWith('image/') && !files.includes(file)) {
      files.push(file)
    }
  }

  return files
}

const handleMentionDeletion = (e) => {
  if (e.key !== 'Backspace' && e.key !== 'Delete') return false

  const currentValue = getEditorRawValue()
  const selectionRange = getRawSelectionRange()
  const isCollapsedBackspace = e.key === 'Backspace' && selectionRange.collapsed
  const domMentionRange = isCollapsedBackspace ? getDomMentionDeletionRange() : null
  const expandedRange = expandMentionDeletionRange(
    currentValue,
    selectionRange.start,
    selectionRange.end,
    isCollapsedBackspace ? 'backward' : 'forward',
    domMentionRange ? [domMentionRange] : []
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
  cleanupDetachedEditorMentions()

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

  const imageFiles = canAcceptUploadFiles() ? getImageFilesFromClipboard(e.clipboardData) : []
  if (imageFiles.length) {
    e.preventDefault()
    emit('paste-images', imageFiles)
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
const mentionItems = ref({ sessions: [], files: [], knowledgeBases: [], mcps: [], skills: [], agents: [] })
const sessionSearchLoading = ref(false)
const sessionSearchError = ref(false)
const mentionSelectedIndex = ref(0)
const searchRequestId = ref(0)
const isComposing = ref(false)
let activeAbortController = null
let mentionSearchTimer = null

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
    if (mentionEnabled.value && !addMenuRange.value) {
      nextTick(() => {
        if (!addMenuRange.value) checkMentionTrigger()
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

watch(
  () => props.mention,
  () => inputRef.value?.querySelectorAll('[data-vue-mention]').forEach(renderEditorMention),
  { deep: true }
)

// 监听输入值变化
watch(inputValue, (value) => {
  if (value !== lastSyncedEditorValue) {
    renderEditorContent(value || '')
  }

  if (debounceTimer.value) {
    clearTimeout(debounceTimer.value)
  }
  if (!value) {
    nextTick(adjustTextareaHeight)
    return
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
  unmountEditorMentions()
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
    mentionPopupVisible.value = false
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
  border-radius: 1.4rem;
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
    gap: 0px;
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
    color: var(--gray-300);
  }

  &.mention-editor {
    position: relative;

    &:empty::before {
      content: attr(data-placeholder);
      position: absolute;
      left: 0;
      top: 0;
      color: var(--gray-300);
      pointer-events: none;
    }
  }

  &[contenteditable='false'] {
    cursor: not-allowed;
  }
}

.send-button-container {
  grid-area: send;
  display: flex;
  align-items: center;
  justify-content: center;
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

:global(:root.dark .input-box) {
  border: 0;
  box-shadow: none;
  transition: background-color 0.15s ease;
}

:global(:root.dark .input-box:not(.dragging-files)) {
  background: var(--gray-30);
}

:global(:root.dark .input-box:not(.dragging-files):hover) {
  background: var(--gray-40);
}

:global(:root.dark .input-box:not(.dragging-files):focus-within) {
  background: var(--gray-50);
}

:global(:root.dark .send-button.ant-btn-icon-only) {
  background: var(--gray-1000);
  color: var(--bg-page);
  box-shadow: none;
}

:global(:root.dark .send-button.ant-btn-icon-only:hover:not(:disabled)) {
  background: var(--gray-800);
  color: var(--bg-page);
  box-shadow: none;
}

:global(:root.dark .send-button.ant-btn-icon-only:active:not(:disabled)) {
  background: var(--gray-700);
  box-shadow: none;
}

:global(:root.dark .send-button.ant-btn-icon-only:focus-visible) {
  outline: 2px solid var(--gray-400);
  outline-offset: 3px;
}

:global(:root.dark .mention-popup) {
  background: var(--color-bg-elevated);
  border: 0;
  box-shadow: none;
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
  max-height: 320px;
  overflow-y: auto;
  background: var(--gray-0);
  border-radius: 16px;
  box-shadow: 0 2px 8px var(--shadow-1);
  border: 1px solid var(--gray-200);
  padding: 12px;

  .session-search-hint {
    margin-left: auto;
    font-size: 11px;
    font-weight: 400;
  }

  .session-search-error {
    padding: 8px 12px;
    color: var(--color-text-secondary);
    font-size: 12px;

    button {
      margin-left: 8px;
      border: 0;
      background: transparent;
      color: var(--main-700);
      cursor: pointer;
    }
  }

  .session-mention-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: calc(100% - 8px);
    min-height: 36px;
    border: 0;
    background: transparent;
    text-align: left;
    font-family: inherit;

    svg { flex-shrink: 0; }

    &:focus-visible {
      outline: 2px solid var(--main-500);
      outline-offset: -2px;
    }
  }

  .session-mention-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .session-mention-date {
    flex-shrink: 0;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

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
    margin-bottom: 2px;
  }

  .add-action {
    width: calc(100% - 8px);
    border: 0;
    background: transparent;
    text-align: left;
    font-family: inherit;
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
      padding: 6px 4px;
      border-radius: 10px;

      .resource-name {
        color: var(--gray-800);
        font-weight: 500;
        flex: 0 0 auto;
        max-width: 50%;
        overflow: hidden;
        text-overflow: ellipsis;
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
    color: var(--color-warning-700);
    font-weight: 700;
  }

  .mention-search-placeholder {
    padding: 4px 8px;
    color: var(--gray-400);
    font-size: 13px;
  }
}
</style>
