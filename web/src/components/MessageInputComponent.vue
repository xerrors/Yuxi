<template>
  <div class="input-box" :class="customClasses" @click="focusInput">
    <div class="top-slot">
      <slot name="top"></slot>
    </div>

    <div class="expand-options" v-if="hasOptionsLeft">
      <a-popover
        v-model:open="optionsExpanded"
        placement="bottomLeft"
        trigger="click"
        :overlay-inner-style="{ padding: '4px' }"
      >
        <template #content>
          <slot name="options-left">
            <div class="no-options">没有配置 options</div>
          </slot>
        </template>
        <a-button type="text" class="expand-btn">
          <template #icon>
            <Paperclip :size="16" :class="{ rotated: optionsExpanded }" />
          </template>
        </a-button>
      </a-popover>
      <slot name="actions-left"></slot>
    </div>

    <div
      ref="inputRef"
      :contenteditable="!disabled"
      class="user-input"
      :placeholder="placeholder"
      @keydown="handleKeyPress"
      @keyup="handleKeyUp"
      @input="handleInput"
      @focus="focusInput"
      @click="handleTextareaClick"
    ></div>

    <!-- @ 提及选择弹窗 -->
    <div v-if="mentionPopupVisible" ref="mentionDropdownRef" class="mention-dropdown-wrapper">
      <div class="mention-popup">
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
                <component
                  :is="item.is_dir ? FolderFilled : getFileIcon(item.label)"
                  :style="{ color: item.is_dir ? '#ffa940' : getFileIconColor(item.label) }"
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
            :class="['mention-item', { active: isItemSelected('knowledge', index) }]"
            @click="insertMention(item)"
          >
            <span
              v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
              :key="pIdx"
              :class="{ 'query-match': part.isMatch }"
              >{{ part.text }}</span
            >
          </div>
        </div>

        <!-- MCP 列表 -->
        <div v-if="mentionItems.mcps.length > 0" class="mention-group">
          <div class="mention-group-title">MCP</div>
          <div
            v-for="(item, index) in mentionItems.mcps"
            :key="'mcp-' + item.value"
            :class="['mention-item', { active: isItemSelected('mcp', index) }]"
            @click="insertMention(item)"
          >
            <span
              v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
              :key="pIdx"
              :class="{ 'query-match': part.isMatch }"
              >{{ part.text }}</span
            >
          </div>
        </div>

        <!-- Skills 列表 -->
        <div v-if="mentionItems.skills.length > 0" class="mention-group">
          <div class="mention-group-title">Skills</div>
          <div
            v-for="(item, index) in mentionItems.skills"
            :key="'skill-' + item.value"
            :class="['mention-item', { active: isItemSelected('skill', index) }]"
            @click="insertMention(item)"
          >
            <span
              v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
              :key="pIdx"
              :class="{ 'query-match': part.isMatch }"
              >{{ part.text }}</span
            >
          </div>
        </div>

        <!-- Subagents 列表 -->
        <div v-if="mentionItems.subagents.length > 0" class="mention-group">
          <div class="mention-group-title">Subagents</div>
          <div
            v-for="(item, index) in mentionItems.subagents"
            :key="'subagent-' + item.value"
            :class="['mention-item', { active: isItemSelected('subagent', index) }]"
            @click="insertMention(item)"
          >
            <span
              v-for="(part, pIdx) in splitTextByQuery(item.label, mentionQuery)"
              :key="pIdx"
              :class="{ 'query-match': part.isMatch }"
              >{{ part.text }}</span
            >
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
import { ref, computed, onMounted, nextTick, watch, onBeforeUnmount, useSlots } from 'vue'
import { SendOutlined, ArrowUpOutlined, PauseOutlined, FolderFilled } from '@ant-design/icons-vue'
import { Paperclip } from 'lucide-vue-next'
import { searchMentionFiles } from '@/apis/mention_api'
import { getFileIcon, getFileIconColor } from '@/utils/file_utils'

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
  }
})

const emit = defineEmits(['update:modelValue', 'send', 'keydown'])
const slots = useSlots()

// @ 提及功能是否启用
const mentionEnabled = computed(() => {
  return !!props.mention
})

const mentionTypePrefixMap = {
  file: 'file',
  knowledge: 'knowledge',
  mcp: 'mcp',
  skill: 'skill',
  subagent: 'subagent'
}

const formatMentionToken = (type, value) => {
  const prefix = mentionTypePrefixMap[type] || type
  return `@${prefix}:${value}`
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

// 高性能且安全的关键字切片高亮解析函数 (100% 防御 XSS，避开危险的 v-html)
const splitTextByQuery = (text, query) => {
  if (!text) return []
  if (!query) return [{ text, isMatch: false }]

  const escapedQuery = query.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')
  const regex = new RegExp(`(${escapedQuery})`, 'gi')
  const parts = text.split(regex)

  return parts.map((part) => ({
    text: part,
    isMatch: part.toLowerCase() === query.toLowerCase()
  }))
}

// 安全获取当前光标 Selection 与 Range 信息
const getActiveRangeInfo = () => {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return null
  return selection.getRangeAt(0)
}

// 依据类型与后缀名获取对应高辨识度的精致 Emoji 图标
const getMentionEmoji = (item) => {
  if (item.type === 'file') {
    if (item.is_dir) return '📁'
    const ext = String(item.label || '')
      .split('.')
      .pop()
      ?.toLowerCase()
    switch (ext) {
      case 'py':
        return '🐍'
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return '📁'
      case 'html':
        return '🌐'
      case 'css':
      case 'less':
      case 'sass':
      case 'scss':
        return '🎨'
      case 'json':
        return '📦'
      case 'md':
        return '📝'
      case 'csv':
        return '📊'
      default:
        return '📄'
    }
  }
  if (item.type === 'knowledge') return '📚'
  if (item.type === 'mcp') return '🔌'
  if (item.type === 'skill') return '⚡'
  if (item.type === 'subagent') return '🤖'
  return '🔗'
}

// 检测是否在 @ 触发位置
const checkMentionTrigger = () => {
  if (!mentionEnabled.value) return false
  const range = getActiveRangeInfo()
  if (!range) return false

  // 确保当前光标聚焦在我们的输入框内部
  if (!inputRef.value || !inputRef.value.contains(range.startContainer)) return false

  let textBeforeCursor = ''
  if (range.startContainer.nodeType === Node.TEXT_NODE) {
    textBeforeCursor = range.startContainer.textContent.slice(0, range.startOffset)
  } else {
    textBeforeCursor = range.startContainer.textContent.slice(0, range.startOffset)
  }

  // 检查是否以 @ 结尾（刚输入 @）或 @ 后有内容
  const atMatch = textBeforeCursor.match(/@(\S*)$/)
  if (atMatch) {
    mentionQuery.value = atMatch[1]
    mentionPopupVisible.value = true
    mentionSelectedIndex.value = 0
    updateMentionItems(mentionQuery.value)
    return true
  }

  mentionPopupVisible.value = false
  return false
}

// 更新提及候选项
const updateMentionItems = (query = '') => {
  if (!props.mention) {
    mentionItems.value = { files: [], knowledgeBases: [], mcps: [], skills: [], subagents: [] }
    return
  }

  const lowerQuery = query.toLowerCase()
  const { files = [], knowledgeBases = [], mcps = [], skills = [], subagents = [] } = props.mention

  const filterItems = (list) =>
    list.filter((item) => {
      const searchTexts = [
        item.label,
        item.value,
        item.description,
        item.tokenLabel,
        item.type,
        mentionTypePrefixMap[item.type]
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

  const filteredLocalFiles = query ? filterItems(localFileItems) : []

  const knowledgeItems = knowledgeBases.map((kb) => {
    const kbName = kb.name || ''
    return {
      value: kbName,
      label: kbName,
      type: 'knowledge',
      insertValue: kbName,
      tokenLabel: formatMentionToken('knowledge', kbName),
      description: kb.db_id
    }
  })

  const mcpItems = mcps.map((m) => {
    const mcpName = m.name || ''
    return {
      value: mcpName,
      label: mcpName,
      type: 'mcp',
      insertValue: mcpName,
      tokenLabel: formatMentionToken('mcp', mcpName),
      description: m.description || ''
    }
  })

  const skillItems = skills.map((skill) => {
    const skillValue = skill.slug || skill.name || skill.id || ''
    return {
      value: skillValue,
      label: skillValue,
      type: 'skill',
      insertValue: skillValue,
      tokenLabel: formatMentionToken('skill', skillValue),
      description: skill.description || ''
    }
  })

  const subagentItems = subagents.map((subagent) => {
    const subagentValue = subagent.id || subagent.value || subagent.name || ''
    const subagentLabel = subagent.name || subagent.label || subagentValue
    return {
      value: subagentValue,
      label: subagentLabel,
      type: 'subagent',
      insertValue: subagentValue,
      tokenLabel: formatMentionToken('subagent', subagentValue),
      description: subagent.description || ''
    }
  })

  // 初始化设置 mentionItems 状态（使用前端已有的本地过滤结果，瞬间更新，达到零卡顿）
  mentionItems.value = {
    files: filteredLocalFiles,
    knowledgeBases: filterItems(knowledgeItems),
    mcps: filterItems(mcpItems),
    skills: filterItems(skillItems),
    subagents: filterItems(subagentItems)
  }

  // NOTE: 如果是尚未开始对话的全新会话，此时 threadId 为空，允许使用临时占位 ID 检索用户全局的工作区文件
  if (query) {
    const activeThreadId = props.threadId || 'new_thread_placeholder'
    clearTimeout(mentionSearchTimer)
    mentionSearchTimer = setTimeout(async () => {
      // 物理中断之前的未完成 HTTP 请求
      if (activeAbortController) {
        activeAbortController.abort()
      }
      activeAbortController = new AbortController()

      searchRequestId.value++
      const currentId = searchRequestId.value

      try {
        const responseData = await searchMentionFiles(
          activeThreadId,
          query,
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
              is_dir: f.is_dir
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

// 将提及项作为精致 HTML 小药丸节点精准插入富文本框中
const insertMention = (item) => {
  const range = getActiveRangeInfo()
  if (!range || !inputRef.value) return

  // 1. 确保光标处于我们的输入框内部
  if (!inputRef.value.contains(range.startContainer)) return

  // 2. 找到光标前面的 @ 文本并移除
  let textNode = range.startContainer
  if (textNode.nodeType !== Node.TEXT_NODE) {
    textNode = document.createTextNode('')
    range.insertNode(textNode)
  }

  const offset = range.startOffset
  const text = textNode.textContent
  const textBefore = text.slice(0, offset)
  const textAfter = text.slice(offset)

  const atIndex = textBefore.lastIndexOf('@')
  if (atIndex === -1) return

  // 替换文本节点内容为 @ 之前的文本
  textNode.textContent = textBefore.slice(0, atIndex)

  // 3. 动态组装带有 contenteditable="false" 的药丸节点，避免受到后续文字录入的碎裂干扰
  const pill = document.createElement('span')
  pill.className = `mention-pill ${item.type}-pill`
  pill.setAttribute('contenteditable', 'false')
  pill.setAttribute('data-type', item.type)
  pill.setAttribute('data-value', item.value)
  pill.setAttribute('data-label', item.label)

  const iconContainer = document.createElement('span')
  iconContainer.className = 'pill-icon'
  iconContainer.textContent = getMentionEmoji(item)
  pill.appendChild(iconContainer)

  const textContainer = document.createElement('span')
  textContainer.className = 'pill-text'
  textContainer.textContent = item.label
  pill.appendChild(textContainer)

  // 4. 插入药丸标签及尾随不折行空格（\u00A0），确保后面的新输入字词与药丸不黏贴
  const spaceNode = document.createTextNode('\u00A0')

  const insertRange = document.createRange()
  insertRange.setStartAfter(textNode)
  insertRange.setEndAfter(textNode)

  insertRange.insertNode(spaceNode)
  insertRange.insertNode(pill)

  if (textAfter) {
    const afterNode = document.createTextNode(textAfter)
    insertRange.setStartAfter(spaceNode)
    insertRange.insertNode(afterNode)
  }

  // 5. 将光标顺滑移动到空格之后
  const newSelection = window.getSelection()
  if (newSelection) {
    newSelection.removeAllRanges()
    const newRange = document.createRange()
    newRange.setStartAfter(spaceNode)
    newRange.collapse(true)
    newSelection.addRange(newRange)
  }

  // 6. 激活输入更新，进行全局同步
  handleInput()

  mentionPopupVisible.value = false
  mentionQuery.value = ''
}

// 滚动到选中项
const scrollToItem = (index) => {
  nextTick(() => {
    const popup = mentionDropdownRef.value?.querySelector('.mention-popup')
    if (!popup) return

    const items = popup.querySelectorAll('.mention-item')
    const selectedItem = items[index]

    if (selectedItem) {
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

// 发送前内容序列化拦截：将富文本 DOM 翻译为后端可以直接消费的带完整绝对路径的纯文本
const serializeContent = () => {
  if (!inputRef.value) return ''

  let result = ''
  const childNodes = inputRef.value.childNodes
  for (let i = 0; i < childNodes.length; i++) {
    const node = childNodes[i]
    if (node.nodeType === Node.TEXT_NODE) {
      result += node.textContent
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.classList.contains('mention-pill')) {
        const type = node.getAttribute('data-type')
        const value = node.getAttribute('data-value')
        result += formatMentionToken(type, value)
      } else if (node.tagName === 'BR') {
        result += '\n'
      } else {
        result += node.textContent
      }
    }
  }
  // 统一将 \u00A0 还原为常规的普通空格发送给后端
  return result.replace(/\u00A0/g, ' ')
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

  // 退格键拦截，如果光标紧跟在药丸后面，退格直接删除整个药丸
  if (e.key === 'Backspace') {
    const range = getActiveRangeInfo()
    if (range && range.collapsed) {
      const container = range.startContainer
      const offset = range.startOffset

      // 场景 A：光标在文本节点的最开始位置，检测前面的兄弟节点是否是药丸
      if (container.nodeType === Node.TEXT_NODE && offset === 0) {
        const prevSibling = container.previousSibling
        if (
          prevSibling &&
          prevSibling.nodeType === Node.ELEMENT_NODE &&
          prevSibling.classList.contains('mention-pill')
        ) {
          e.preventDefault()
          prevSibling.remove()
          handleInput()
          return
        }
      }
      // 场景 B：光标在元素容器节点，偏移量前面直接是药丸节点
      else if (container.nodeType === Node.ELEMENT_NODE && offset > 0) {
        const targetNode = container.childNodes[offset - 1]
        if (
          targetNode &&
          targetNode.nodeType === Node.ELEMENT_NODE &&
          targetNode.classList.contains('mention-pill')
        ) {
          e.preventDefault()
          targetNode.remove()
          handleInput()
          return
        }
      }
    }
  }

  emit('keydown', e)
}

// 检测 @ 触发
const handleKeyUp = (e) => {
  if (e.key === '@' && mentionEnabled.value) {
    nextTick(() => {
      checkMentionTrigger()
    })
  }
}

// 处理输入事件
const handleInput = (e) => {
  // 防呆：如果输入框全空（仅有空白字符且无提及药丸），物理重置 innerHTML 保证 CSS :empty 能够精准唤醒
  if (inputRef.value) {
    const text = inputRef.value.textContent || ''
    const hasPill = inputRef.value.querySelector('.mention-pill')
    if (text.trim() === '' && !hasPill) {
      inputRef.value.innerHTML = ''
    }
  }

  const value = serializeContent()
  emit('update:modelValue', value)

  if (mentionEnabled.value) {
    nextTick(() => {
      checkMentionTrigger()
    })
  }
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
let activeAbortController = null
let mentionSearchTimer = null

// 聚焦输入框，智能把光标归位移到最后
const focusInput = () => {
  if (inputRef.value && !props.disabled) {
    inputRef.value.focus()

    // 把光标移动到富文本框的最末尾，提供极爽的使用反馈
    try {
      const selection = window.getSelection()
      if (selection) {
        const range = document.createRange()
        range.selectNodeContents(inputRef.value)
        range.collapse(false)
        selection.removeAllRanges()
        selection.addRange(range)
      }
    } catch (err) {
      console.warn('Failed to set selection range:', err)
    }

    if (mentionEnabled.value) {
      nextTick(() => {
        checkMentionTrigger()
      })
    }
  }
}

// 处理输入框点击事件，自适应检测光标是否落入 @提及 范围内以唤醒或更新弹窗
const handleTextareaClick = (e) => {
  if (mentionEnabled.value) {
    nextTick(() => {
      checkMentionTrigger()
    })
  }
}

// 监听父组件传进来的 modelValue 变化，支持发送完毕后的物理清空
watch(
  () => props.modelValue,
  (newVal) => {
    if (!newVal) {
      if (inputRef.value && inputRef.value.innerHTML !== '') {
        inputRef.value.innerHTML = ''
      }
    }
  }
)

onMounted(() => {
  document.addEventListener('click', closeMentionPopup)
  nextTick(() => {
    if (inputRef.value) {
      inputRef.value.focus()
    }
  })
})

// 组件卸载时清除定时器和事件监听器
onBeforeUnmount(() => {
  if (debounceTimer.value) {
    clearTimeout(debounceTimer.value)
  }
  if (mentionSearchTimer) {
    clearTimeout(mentionSearchTimer)
  }
  if (activeAbortController) {
    activeAbortController.abort()
  }
  document.removeEventListener('click', closeMentionPopup)
})

// 公开方法供父组件调用
defineExpose({
  focus: () => inputRef.value?.focus(),
  closeOptions: () => {
    optionsExpanded.value = false
  }
})
</script>

<style lang="less" scoped>
.input-box {
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
  line-height: 1.5;
  font-family: inherit;
  min-height: 44px; /* Default min-height for multi-line */
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;

  &:focus {
    outline: none;
    box-shadow: none;
  }

  // 完美的 Placeholder 伪类，自适应 contenteditable:empty
  &:empty::before {
    content: attr(placeholder);
    color: var(--gray-400);
    pointer-events: none;
    display: block;
  }
}

// 药丸全局样式 (使用 :deep 穿透动态插入的节点)
:deep(.mention-pill) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 6px;
  padding: 1px 6px;
  margin: 0 2px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  vertical-align: middle;
  cursor: default;
  user-select: none;
  transition: all 0.2s ease;

  &:hover {
    filter: brightness(0.96);
  }

  .pill-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
  }

  .pill-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 200px;
  }

  // 各种提及类型的专属配色加持
  &.file-pill {
    background-color: rgba(9, 109, 217, 0.06);
    border: 1px solid rgba(9, 109, 217, 0.16);
    color: var(--main-600);
  }

  &.knowledge-pill {
    background-color: rgba(82, 196, 26, 0.06);
    border: 1px solid rgba(82, 196, 26, 0.16);
    color: #389e0d;
  }

  &.mcp-pill {
    background-color: rgba(114, 46, 209, 0.06);
    border: 1px solid rgba(114, 46, 209, 0.16);
    color: #531dab;
  }

  &.skill-pill {
    background-color: rgba(250, 140, 22, 0.06);
    border: 1px solid rgba(250, 140, 22, 0.16);
    color: #d46b08;
  }

  &.subagent-pill {
    background-color: rgba(19, 194, 194, 0.06);
    border: 1px solid rgba(19, 194, 194, 0.16);
    color: #08979c;
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
  transition: all 0.2s ease;
  border: 1px solid transparent;
  background-color: transparent;

  &:hover {
    color: var(--main-color);
  }

  &:active {
    color: var(--main-color);
    // 移除点击缩小效果
  }

  .anticon {
    font-size: 14px;
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);

    &.rotated {
      transform: rotate(45deg);
    }
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

    &:hover,
    &.active {
      background-color: var(--main-10);
      color: var(--main-600);

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
