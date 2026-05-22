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
              @mousedown.prevent.stop="insertMention(item)"
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
            @mousedown.prevent.stop="insertMention(item)"
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
            @mousedown.prevent.stop="insertMention(item)"
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
            @mousedown.prevent.stop="insertMention(item)"
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
            @mousedown.prevent.stop="insertMention(item)"
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
import { useChatUIStore } from '@/stores/chatUI'

const chatUIStore = useChatUIStore()

// NOTE: 极其重要的安全锁 - 用于在打字触发提及的瞬间锁定 @ 符号及其后查询串的 Range 区间，彻底规避浏览器失焦导致的药丸插入偏离或失效
const mentionTriggerRange = ref(null)

/**
 * 依据全局字符偏移量，在 DOM 容器树中深度优先定位对应的真正文本节点与节点内的偏移量
 * @param container 容器元素
 * @param targetOffset 全局字符偏移量
 */
const findNodeAndOffsetAt = (container, targetOffset) => {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false)
  let currentOffset = 0
  let node = walker.nextNode()
  while (node) {
    const len = node.textContent.length
    if (currentOffset + len >= targetOffset) {
      return { node, offset: targetOffset - currentOffset }
    }
    currentOffset += len
    node = walker.nextNode()
  }
  return { node: container, offset: container.childNodes.length }
}

// 点击外部关闭下拉框
const mentionDropdownRef = ref(null)
const closeMentionPopup = (e) => {
  if (!mentionPopupVisible.value) return
  if (inputRef.value?.contains(e.target)) return
  if (mentionDropdownRef.value?.contains(e.target)) return
  mentionPopupVisible.value = false
  mentionTriggerRange.value = null // 清空锁定的 Range，防内存泄漏并安全重置
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

// 依据类型与后缀名获取对应的高清晰度极简线性矢量 SVG 图标或极客 CSS 迷你代码行
const getMentionIconSvg = (item) => {
  if (item.type === 'file') {
    if (item.is_dir) {
      // 文件夹极细线框
      return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
    }
    
    // 智能提取文件扩展名并检测是否为代码文件类型
    const name = (item.label || '').toLowerCase()
    const codeExtensions = [
      '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.vue', '.css', '.less', '.html', 
      '.cpp', '.c', '.h', '.cc', '.java', '.go', '.sh', '.yaml', '.yml', '.md', 
      '.rs', '.sql', '.toml', '.xml', '.ini', '.bat', '.ps1'
    ]
    const isCode = codeExtensions.some(ext => name.endsWith(ext))
    
    if (isCode) {
      // 极致科技感的极客 CSS 迷你语法高亮代码行 (三色线条发光渲染)
      return `<div class="mini-code-icon">
        <span class="mini-code-line mini-code-line-1"></span>
        <span class="mini-code-line mini-code-line-2"></span>
        <span class="mini-code-line mini-code-line-3"></span>
      </div>`
    }
    
    // 普通文件的 1.8 极细描边纸张线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`
  }
  if (item.type === 'knowledge') {
    // 知识库/书本的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>`
  }
  if (item.type === 'mcp') {
    // MCP 插头的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8h12z"></path><path d="M9 8V2h6v6M12 22v-5"></path></svg>`
  }
  if (item.type === 'skill') {
    // 技能闪电的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>`
  }
  if (item.type === 'subagent') {
    // 智能体机器人的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><rect x="3" y="11" width="18" height="10" rx="2"></rect><circle cx="12" cy="5" r="2"></circle><path d="M12 7v4M8 16h.01M16 16h.01"></path></svg>`
  }
  // 兜底极细矢量链接图标
  return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>`
}

// 检测是否在 @ 触发位置
// 检测是否在 @ 触发位置并精确锁定 Range 范围
const checkMentionTrigger = () => {
  if (!mentionEnabled.value) return false
  const range = getActiveRangeInfo()
  if (!range) return false

  // 确保当前光标聚焦在我们的输入框内部
  if (!inputRef.value || !inputRef.value.contains(range.startContainer)) return false

  let textBeforeCursor = ''
  let cursorGlobalIndex = 0
  try {
    const tempRange = range.cloneRange()
    tempRange.setStart(inputRef.value, 0)
    textBeforeCursor = tempRange.toString()
    cursorGlobalIndex = textBeforeCursor.length
  } catch (err) {
    console.warn('Failed to get text before cursor using Range:', err)
    // 兜底退化处理
    const content = inputRef.value.textContent || ''
    textBeforeCursor = content
    cursorGlobalIndex = content.length
  }

  // 检查是否以 @ 结尾（刚输入 @）或 @ 后有内容
  const atMatch = textBeforeCursor.match(/@(\S*)$/)
  if (atMatch) {
    mentionQuery.value = atMatch[1]
    mentionPopupVisible.value = true
    mentionSelectedIndex.value = 0

    // NOTE: 核心黄金锁 - 精确计算全局 @query 字符串区间并在 DOM 树中锁定
    try {
      const atGlobalIndex = cursorGlobalIndex - atMatch[0].length
      const startPos = findNodeAndOffsetAt(inputRef.value, atGlobalIndex)
      const endPos = findNodeAndOffsetAt(inputRef.value, cursorGlobalIndex)

      const triggerRange = document.createRange()
      triggerRange.setStart(startPos.node, startPos.offset)
      triggerRange.setEnd(endPos.node, endPos.offset)

      mentionTriggerRange.value = triggerRange
    } catch (err) {
      console.warn('Failed to lock mention trigger range:', err)
      mentionTriggerRange.value = null // 失败则退化为即时搜索
    }

    updateMentionItems(mentionQuery.value)
    return true
  }

  mentionPopupVisible.value = false
  mentionTriggerRange.value = null
  return false
}

// 记录上一次实际触发远程搜索的 query 字符串，防无意义的重复搜索网络开销
let lastSearchQuery = ''

// 更新提及候选项
const updateMentionItems = (query = '') => {
  if (!props.mention) {
    mentionItems.value = { files: [], knowledgeBases: [], mcps: [], skills: [], subagents: [] }
    return
  }

  // 如果搜索内容与上一次完全一致，且弹窗已经在显示，直接退出，绝不重新发送重复的 API 请求，也防止本地过滤覆盖已有的远程搜索结果
  if (query && query === lastSearchQuery && mentionPopupVisible.value) {
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

  if (!query) {
    lastSearchQuery = ''
  }

  // NOTE: 如果是尚未开始对话的全新会话，此时 threadId 为空，允许使用临时占位 ID 检索用户全局的工作区文件
  if (query) {
    const activeThreadId = props.threadId || 'new_thread_placeholder'
    clearTimeout(mentionSearchTimer)
    mentionSearchTimer = setTimeout(async () => {
      lastSearchQuery = query
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
  // 0. 立即物理熔断提及状态，清除防抖，打断任何挂起中的远程请求，确保后续 DOM 更新时决不误唤醒
  mentionPopupVisible.value = false
  mentionQuery.value = ''
  lastSearchQuery = ''
  if (mentionSearchTimer) {
    clearTimeout(mentionSearchTimer)
    mentionSearchTimer = null
  }
  if (activeAbortController) {
    activeAbortController.abort()
    activeAbortController = null
  }

  // 1. 优先使用在打字阶段就已经精准锁定的 mentionTriggerRange，双重保险防失焦和偏移
  let range = mentionTriggerRange.value
  if (!range) {
    // 兜底退化：如果无锁定的 Range，则即时获取
    range = getActiveRangeInfo()
  }
  if (!range || !inputRef.value) return

  // 确保范围在我们的输入框内部
  if (!inputRef.value.contains(range.startContainer)) return

  // 2. 原地擦除：干净利索地把已匹配好的整个 "@query" 文本从 DOM 中删除
  try {
    range.deleteContents()
  } catch (err) {
    console.warn('Failed to delete query contents using Range:', err)
  }

  // 3. 动态组装带有 contenteditable="false" 的药丸节点，避免受到后续文字录入的碎裂干扰
  const pill = document.createElement('span')
  pill.className = `mention-pill ${item.type}-pill`
  pill.setAttribute('contenteditable', 'false')
  pill.setAttribute('data-type', item.type)
  pill.setAttribute('data-value', item.value)
  pill.setAttribute('data-label', item.label)

  const iconContainer = document.createElement('span')
  iconContainer.className = 'pill-icon'
  iconContainer.innerHTML = getMentionIconSvg(item)
  pill.appendChild(iconContainer)

  const textContainer = document.createElement('span')
  textContainer.className = 'pill-text'
  textContainer.textContent = item.label
  pill.appendChild(textContainer)

  const deleteBtn = document.createElement('span')
  deleteBtn.className = 'pill-close'
  deleteBtn.innerHTML = `<svg viewBox="0 0 24 24" width="10" height="10" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`
  pill.appendChild(deleteBtn)

  // 4. 插入药丸标签及尾随不折行空格（\u00A0），确保后面的新输入字词与药丸不黏贴
  const spaceNode = document.createTextNode('\u00A0')

  try {
    // 注意：insertNode 会把元素插在 Range 的起点。我们先插空格，再插药丸，即可保证最终物理顺序为 [药丸][空格]
    range.insertNode(spaceNode)
    range.insertNode(pill)
  } catch (err) {
    console.warn('Failed to insert mention nodes:', err)
    // 降级使用 appendChild 兜底
    inputRef.value.appendChild(pill)
    inputRef.value.appendChild(spaceNode)
  }

  // 5. 将光标顺滑移动到空格之后，无缝支持继续录入
  try {
    const newSelection = window.getSelection()
    if (newSelection) {
      newSelection.removeAllRanges()
      const newRange = document.createRange()
      newRange.setStartAfter(spaceNode)
      newRange.collapse(true)
      newSelection.addRange(newRange)
    }
  } catch (err) {
    console.warn('Failed to redirect cursor after space node:', err)
  }

  // 6. 激活输入更新，进行全局同步
  handleInput()

  mentionPopupVisible.value = false
  mentionQuery.value = ''
  mentionTriggerRange.value = null // 完成后重置锁定
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
  if (!mentionEnabled.value) return

  // 1. 如果输入了 @，立刻检测并唤醒提及
  // 2. 如果使用方向键/Home/End 移动了光标，为了与鼠标点击切换光标保持行为一致，也自适应检测
  // 注意：当提及弹窗显示时，ArrowUp 和 ArrowDown 用于列表项的键盘导航，此时输入框光标并未在文本中实质位移，无需重复检测
  const isCursorMovement = ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) ||
    (['ArrowUp', 'ArrowDown'].includes(e.key) && !mentionPopupVisible.value)

  if (e.key === '@' || isCursorMovement) {
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
const focusInput = (e) => {
  if (inputRef.value && !props.disabled) {
    // NOTE: 如果输入框已经获得焦点，且点击是发生在输入框内部（如文字中间、药丸之间），
    // 此时用户意图是在文字中间精细定位光标，我们必须立刻返回，绝对不能强行重置光标到末尾！
    const isAlreadyFocused = document.activeElement === inputRef.value
    const isClickInsideInput = e && inputRef.value.contains(e.target)

    if (isAlreadyFocused && isClickInsideInput) {
      if (mentionEnabled.value) {
        nextTick(() => {
          checkMentionTrigger()
        })
      }
      return
    }

    // 只有在未聚焦，或者点击了外层容器空白处时，我们才主动聚焦并将光标智能定位到末尾
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
  // 1. 优先检测并拦截是否点击了药丸的精致删除按钮
  const closeBtn = e.target.closest('.pill-close')
  if (closeBtn) {
    e.preventDefault()
    e.stopPropagation()
    const pill = closeBtn.closest('.mention-pill')
    if (pill) {
      pill.remove()
      handleInput() // 重新序列化并触发数据同步
    }
    return
  }

  // 2. 智能检测是否点击了文件小药丸
  const filePill = e.target.closest('.mention-pill.file-pill')
  if (filePill) {
    e.preventDefault()
    e.stopPropagation() // 强力拦截，防止输入框失去焦点或触发键盘弹起/回退
    const filePath = filePill.getAttribute('data-value')
    if (filePath) {
      chatUIStore.triggerFilePreview(filePath)
    }
    return
  }

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

// 监听提及弹窗可见性变化，在弹窗关闭时自动重置上一次搜索内容，以支持下一次全新输入时重新拉取
watch(mentionPopupVisible, (newVal) => {
  if (!newVal) {
    lastSearchQuery = ''
  }
})

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

// 药丸全局样式 (使用 :deep 穿透动态插入的节点，Option 2: 极致轻薄无界流式皮肤)
:deep(.mention-pill) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 6px;
  height: 22px; /* 绝对锁定药丸物理高度为 22px */
  box-sizing: border-box; /* 启用 border-box 确保总高度严丝合缝 */
  padding: 0 6px; /* 物理高度已锁定，上下 padding 归零，左右保持对称的 6px */
  margin: 0 3px;
  font-size: 13px;
  font-weight: 550; /* 调重字重以在无界/轻量卡片下提供卓越的实体聚焦锚点 */
  line-height: 1; /* 极关键！缩减行高溢出，让 flex align-items 获得绝对垂直控制权 */
  vertical-align: middle;
  cursor: default;
  user-select: none;
  background-color: rgba(0, 0, 0, 0.02); /* 极度清透的微弱半透明底色，去除大色块压迫感 */
  border: 1px solid rgba(0, 0, 0, 0.045); /* 极其隐约的超细淡灰色边框 */
  position: relative; /* 注入相对定位，为删除按钮绝对定位铺垫 */
  // 注入 padding-right 过渡，实现呼吸般的横向展开动画
  transition: 
    background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), 
    border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), 
    padding-right 0.2s cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    background-color: rgba(0, 0, 0, 0.04);
    border-color: rgba(0, 0, 0, 0.08);
    padding-right: 22px; /* Hover 时右侧内边距优雅撑开 16px，供删除按钮浮现而绝不挤压文字 */

    .pill-close {
      opacity: 0.6;
      transform: scale(1); /* 优雅淡入并弹性膨胀归位 */
      pointer-events: auto; /* 仅在显示时允许鼠标事件 */
    }
  }

  // 极客 CSS 迷你高亮代码行样式矩阵
  .mini-code-icon {
    display: inline-flex;
    flex-direction: column;
    gap: 1.5px;
    width: 12px;
    height: 12px;
    justify-content: center;
    align-items: flex-start;
    flex-shrink: 0;
    position: relative;
    top: 0.5px; /* 调低图标 1px，对齐中文视觉重心 */

    .mini-code-line {
      height: 2px;
      border-radius: 1px;
      display: block;

      &.mini-code-line-1 {
        width: 11px;
        background-color: #569cd6; /* VS Code 经典的优雅蓝色 (Keyword 属性) */
        box-shadow: 0 0 3px rgba(86, 156, 214, 0.45); /* 微弱发光光晕以增强 12px 极小空间的色彩立体感 */
      }
      &.mini-code-line-2 {
        width: 7px;
        background-color: #4ec9b0; /* 薄荷绿 (Type/Methods 属性) */
        box-shadow: 0 0 3px rgba(78, 201, 176, 0.45);
      }
      &.mini-code-line-3 {
        width: 9px;
        background-color: #ce9178; /* 橙红色 (Strings/Constants 属性) */
        box-shadow: 0 0 3px rgba(206, 145, 120, 0.45);
      }
    }
  }

  .pill-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    flex-shrink: 0;
    height: 12px; /* 显式物理高度，同 svg 保持绝对一致 */
    position: relative;
    top: 0.5px; /* 调低图标 1px，对齐中文视觉重心 */

    svg {
      width: 12px;
      height: 12px;
      color: inherit;
      display: block;
      transition: transform 0.2s ease;
    }
  }

  .pill-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 200px;
    padding-right: 2px; /* 极其关键的 2px 缓冲，物理容纳英文字体 y, f 等侧斜字母的右溢出笔画，彻底规避 overflow:hidden 强行斩断字体的缺陷 */
    letter-spacing: -0.01em;
    display: inline-block; /* 保证不受行高溢出干扰 */
    line-height: 1; /* 强制单行行高为 1，消除多语言字体包围盒计算偏差 */
    position: relative;
    top: -0.5px; /* 文字稍微往上提，抵消中文 baseline 偏下问题 */
  }

  // 绝对定位的精致删除按钮 (Notion 级隐藏与唤醒)
  .pill-close {
    position: absolute;
    right: 4px;
    top: 3px; /* 22px 容器减去 14px 按钮高度除以 2，得到 3px 的完美物理垂直居中，杜绝 translateY 亚像素偏移 */
    transform: scale(0.7); /* 默认微型缩小，与透明度配合营造优雅浮现感 */
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    color: inherit;
    opacity: 0;
    pointer-events: none; /* 默认未激活时穿透鼠标，彻底防误触 */
    cursor: pointer;
    transition: 
      opacity 0.2s cubic-bezier(0.4, 0, 0.2, 1), 
      transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), 
      background-color 0.2s ease;
    margin-left: 0;

    &:hover {
      opacity: 1 !important;
      background-color: rgba(0, 0, 0, 0.06);
    }

    &:active {
      transform: scale(0.85) !important; /* 点击时物理弹性微按压缩放反馈 */
    }
  }

  // 极客通透色相高亮专属主题配比 (只高亮纯净的前景色，不依赖沉重背景色)
  &.file-pill {
    color: #096dd9; /* 科技深邃蓝 */
    cursor: pointer;

    &:hover {
      background-color: rgba(9, 109, 217, 0.05);
      border-color: rgba(9, 109, 217, 0.15);
    }

    &:active {
      transform: scale(0.96);
    }
  }

  &.knowledge-pill {
    color: #389e0d; /* 薄荷森林绿 */

    &:hover {
      background-color: rgba(82, 196, 26, 0.05);
      border-color: rgba(82, 196, 26, 0.15);
    }
  }

  &.mcp-pill {
    color: #531dab; /* 极客极光紫 */

    &:hover {
      background-color: rgba(114, 46, 209, 0.05);
      border-color: rgba(114, 46, 209, 0.15);
    }
  }

  &.skill-pill {
    color: #d46b08; /* 温暖金橘橙 */

    &:hover {
      background-color: rgba(250, 140, 22, 0.05);
      border-color: rgba(250, 140, 22, 0.15);
    }
  }

  &.subagent-pill {
    color: #08979c; /* 高透青色 */

    &:hover {
      background-color: rgba(19, 194, 194, 0.05);
      border-color: rgba(19, 194, 194, 0.15);
    }
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
