<template>
  <Teleport to="body">
    <Transition name="search-modal" appear>
      <div v-if="open" class="global-search-overlay" @mousedown.self="close">
        <section
          class="global-search-modal"
          role="dialog"
          aria-modal="true"
          :aria-label="activeMode === 'file' ? '搜索文件' : '搜索对话'"
          @keydown.down.prevent="moveSelection(1)"
          @keydown.up.prevent="moveSelection(-1)"
          @keydown.enter.prevent="confirmSelection"
          @keydown.esc.prevent="close"
        >
          <div class="global-search-input-row">
            <input
              ref="searchInputRef"
              v-model="searchText"
              class="global-search-input"
              type="text"
              :placeholder="inputPlaceholder"
              autocomplete="off"
              :aria-label="activeMode === 'file' ? '搜索文件' : '搜索对话'"
            />
            <button type="button" class="global-search-close" aria-label="关闭" @click="close">
              <X :size="20" />
            </button>
          </div>

          <div v-if="modes.length > 1" class="global-search-mode-bar" role="tablist">
            <button
              v-for="mode in modes"
              :key="mode"
              type="button"
              class="global-search-mode-option"
              :class="{ active: activeMode === mode }"
              role="tab"
              :aria-selected="activeMode === mode"
              @click="switchMode(mode)"
            >
              <MessageCircle v-if="mode === 'conversation'" :size="14" class="mode-icon" />
              <File v-else :size="14" class="mode-icon" />
              <span>{{ mode === 'file' ? '文件' : '对话' }}</span>
            </button>
          </div>

          <div
            v-if="activeMode === 'file'"
            ref="resultListRef"
            class="global-search-body"
            @scroll="handleFileResultScroll"
          >
            <div v-if="isSearching && fileResults.length === 0" class="global-search-skeleton">
              <div v-for="index in 5" :key="index" class="skeleton-row">
                <span class="skeleton-dot"></span>
                <span class="skeleton-lines">
                  <i></i>
                  <i></i>
                </span>
              </div>
            </div>

            <div v-else-if="fileResults.length > 0" class="global-search-results">
              <button
                v-for="(item, index) in fileResults"
                :key="item.path"
                type="button"
                class="global-search-result"
                :class="{ selected: selectedIndex === index }"
                @mouseenter="selectedIndex = index"
                @click="selectFileResult(item)"
              >
                <FileTypeIcon :name="item.name" :size="18" class="result-icon" />
                <span class="result-main">
                  <span class="result-title">
                    <template v-for="(part, partIndex) in splitName(item)" :key="partIndex">
                      <mark v-if="part.match">{{ part.text }}</mark>
                      <span v-else>{{ part.text }}</span>
                    </template>
                  </span>
                  <span class="result-path">{{ item.path }}</span>
                </span>
                <span class="result-date">{{ formatResultDate(item.modified_at) }}</span>
              </button>
            </div>

            <div v-else-if="fileSearchError" class="global-search-error">{{ fileSearchError }}</div>

            <div v-else-if="!isSearching" class="global-search-empty">未找到相关文件</div>
          </div>

          <div
            v-else
            ref="resultListRef"
            class="global-search-body"
            @scroll="handleConversationScroll"
          >
            <template v-if="isSearchMode">
              <div
                v-if="isSearching && conversationResults.length === 0"
                class="global-search-skeleton"
              >
                <div v-for="index in 5" :key="index" class="skeleton-row">
                  <span class="skeleton-dot"></span>
                  <span class="skeleton-lines">
                    <i></i>
                    <i></i>
                  </span>
                </div>
              </div>

              <div v-else-if="conversationResults.length > 0" class="global-search-results">
                <button
                  v-for="(item, index) in conversationResults"
                  :key="item.id"
                  type="button"
                  class="global-search-result"
                  :class="{ selected: selectedIndex === index }"
                  @mouseenter="selectedIndex = index"
                  @click="selectConversationResult(item)"
                >
                  <MessageCircle :size="18" class="result-icon" />
                  <span class="result-main">
                    <span class="result-title">{{ item.title || '新的对话' }}</span>
                    <span class="result-snippet">
                      <template v-for="(part, partIndex) in splitSnippet(item)" :key="partIndex">
                        <mark v-if="part.match">{{ part.text }}</mark>
                        <span v-else>{{ part.text }}</span>
                      </template>
                    </span>
                  </span>
                  <span class="result-date">{{
                    formatResultDate(item.latest_match_at || item.updated_at)
                  }}</span>
                </button>
                <div v-if="isLoadingMore" class="global-search-loading-more">加载中...</div>
              </div>

              <div v-else class="global-search-empty">未找到相关对话</div>
            </template>

            <template v-else>
              <button
                type="button"
                class="global-search-default-item"
                :class="{ selected: selectedIndex === 0 }"
                @mouseenter="selectedIndex = 0"
                @click="createThread"
              >
                <MessageCirclePlus :size="18" class="default-icon" />
                <span>新对话</span>
              </button>

              <template v-for="row in recentRows" :key="row.key">
                <div v-if="row.type === 'label'" class="global-search-group-label">
                  {{ row.label }}
                </div>
                <button
                  v-else
                  type="button"
                  class="global-search-default-item"
                  :class="{ selected: selectedIndex === row.actionIndex }"
                  @mouseenter="selectedIndex = row.actionIndex"
                  @click="selectRecentThread(row.thread)"
                >
                  <MessageCircle :size="18" class="default-icon" />
                  <span>{{ row.thread.title || '新的对话' }}</span>
                </button>
              </template>

              <div v-if="recentRows.length === 0" class="global-search-empty default-empty">
                暂无对话历史
              </div>
            </template>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { File, MessageCircle, MessageCirclePlus, X } from '@lucide/vue'
import { threadApi } from '@/apis'
import dayjs, { parseToShanghai } from '@/utils/time'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const CONVERSATION_SEARCH_LIMIT = 20
const RECENT_LIMIT = 30

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  modes: {
    type: Array,
    default: () => ['conversation', 'file']
  },
  defaultMode: {
    type: String,
    default: 'conversation'
  },
  recentThreads: {
    type: Array,
    default: () => []
  },
  fileSearch: {
    type: Function,
    default: null
  },
  filePlaceholder: {
    type: String,
    default: '搜索文件...'
  }
})

const emit = defineEmits([
  'update:open',
  'select-thread',
  'create-thread',
  'thread-found',
  'select-file'
])

const searchInputRef = ref(null)
const resultListRef = ref(null)
const searchText = ref('')
const selectedIndex = ref(0)

const activeMode = ref(props.defaultMode)

// 对话搜索状态
const conversationResults = ref([])
const hasMore = ref(false)
const isSearching = ref(false)
const isLoadingMore = ref(false)
let searchTimer = null
let searchRequestId = 0

// 文件搜索状态
const fileResults = ref([])
const fileSearchError = ref('')
let fileSearchTimer = null
let fileSearchRequestId = 0

const trimmedSearchText = computed(() => searchText.value.trim())
const isSearchMode = computed(() => Boolean(trimmedSearchText.value))

const inputPlaceholder = computed(() =>
  activeMode.value === 'file' ? props.filePlaceholder : '搜索对话...'
)

const sortedRecentThreads = computed(() => {
  return [...props.recentThreads]
    .sort((a, b) => {
      const first = parseToShanghai(a.updated_at || a.created_at)
      const second = parseToShanghai(b.updated_at || b.created_at)
      if (!first && !second) return 0
      if (!first) return 1
      if (!second) return -1
      return second.valueOf() - first.valueOf()
    })
    .slice(0, RECENT_LIMIT)
})

const recentRows = computed(() => {
  const rows = []
  let lastGroup = ''
  let actionIndex = 1
  sortedRecentThreads.value.forEach((thread) => {
    const group = getRecentGroupLabel(thread)
    if (group !== lastGroup) {
      rows.push({ type: 'label', key: `label-${group}`, label: group })
      lastGroup = group
    }
    rows.push({
      type: 'thread',
      key: thread.id,
      thread,
      actionIndex
    })
    actionIndex += 1
  })
  return rows
})

const actionCount = computed(() => {
  if (activeMode.value === 'file') return fileResults.value.length
  if (isSearchMode.value) return conversationResults.value.length
  return 1 + sortedRecentThreads.value.length
})

const resetState = () => {
  searchRequestId += 1
  fileSearchRequestId += 1
  clearTimeout(searchTimer)
  clearTimeout(fileSearchTimer)
  searchText.value = ''
  conversationResults.value = []
  fileResults.value = []
  fileSearchError.value = ''
  hasMore.value = false
  isSearching.value = false
  isLoadingMore.value = false
  selectedIndex.value = 0
}

const close = () => {
  emit('update:open', false)
}

const switchMode = (mode) => {
  if (!props.modes.includes(mode) || mode === activeMode.value) return
  activeMode.value = mode
  resetState()
  nextTick(() => {
    searchInputRef.value?.focus()
  })
}

const createThread = () => {
  emit('create-thread')
  close()
}

const selectRecentThread = (thread) => {
  if (!thread?.id) return
  emit('select-thread', thread)
  close()
}

const selectConversationResult = (item) => {
  if (!item?.id) return
  emit('thread-found', normalizeSearchThread(item))
  emit('select-thread', normalizeSearchThread(item))
  close()
}

const selectFileResult = (item) => {
  if (!item?.path) return
  emit('select-file', item)
  close()
}

const normalizeSearchThread = (item) => ({
  id: item.id || item.thread_id,
  uid: item.uid,
  agent_id: item.agent_id,
  title: item.title,
  is_pinned: Boolean(item.is_pinned),
  created_at: item.created_at,
  updated_at: item.updated_at,
  metadata: item.metadata || {}
})

const moveSelection = (delta) => {
  if (actionCount.value <= 0) return
  selectedIndex.value = (selectedIndex.value + delta + actionCount.value) % actionCount.value
  scrollSelectedIntoView()
}

const confirmSelection = () => {
  if (activeMode.value === 'file') {
    const item = fileResults.value[selectedIndex.value]
    if (item) selectFileResult(item)
    return
  }
  if (isSearchMode.value) {
    const item = conversationResults.value[selectedIndex.value]
    if (item) selectConversationResult(item)
    return
  }
  if (selectedIndex.value === 0) {
    createThread()
    return
  }
  const thread = sortedRecentThreads.value[selectedIndex.value - 1]
  if (thread) selectRecentThread(thread)
}

const scrollSelectedIntoView = () => {
  nextTick(() => {
    const selected = resultListRef.value?.querySelector('.selected')
    selected?.scrollIntoView({ block: 'nearest' })
  })
}

const searchConversations = async ({ reset = false } = {}) => {
  const query = trimmedSearchText.value
  if (!query) {
    conversationResults.value = []
    hasMore.value = false
    selectedIndex.value = 0
    return
  }

  const requestId = ++searchRequestId
  const offset = reset ? 0 : conversationResults.value.length
  if (reset) {
    isSearching.value = true
    hasMore.value = false
  } else {
    isLoadingMore.value = true
  }

  try {
    const response = await threadApi.searchThreads(query, {
      limit: CONVERSATION_SEARCH_LIMIT,
      offset
    })
    if (requestId !== searchRequestId) return
    const items = response?.items || []
    conversationResults.value = reset ? items : [...conversationResults.value, ...items]
    hasMore.value = Boolean(response?.has_more)
    selectedIndex.value =
      conversationResults.value.length > 0
        ? Math.min(selectedIndex.value, conversationResults.value.length - 1)
        : 0
  } catch (error) {
    if (requestId === searchRequestId) {
      console.warn('搜索对话失败:', error)
      conversationResults.value = reset ? [] : conversationResults.value
      hasMore.value = false
    }
  } finally {
    if (requestId === searchRequestId) {
      isSearching.value = false
      isLoadingMore.value = false
    }
  }
}

const handleConversationScroll = () => {
  if (!isSearchMode.value || !hasMore.value || isSearching.value || isLoadingMore.value) return
  const el = resultListRef.value
  if (!el) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80) {
    searchConversations({ reset: false })
  }
}

const searchFiles = async (query) => {
  if (!props.fileSearch) return
  const requestId = ++fileSearchRequestId
  isSearching.value = true
  fileSearchError.value = ''
  try {
    const response = await props.fileSearch(query)
    if (requestId !== fileSearchRequestId) return
    fileResults.value = response?.entries || []
    selectedIndex.value = 0
  } catch (error) {
    if (requestId === fileSearchRequestId) {
      console.warn('搜索文件失败:', error)
      fileResults.value = []
      fileSearchError.value = error?.message || '搜索失败，请重试'
    }
  } finally {
    if (requestId === fileSearchRequestId) isSearching.value = false
  }
}

const handleFileResultScroll = () => {}

const getRecentGroupLabel = (thread) => {
  const parsed = parseToShanghai(thread.updated_at || thread.created_at)
  if (!parsed) return '更早'
  const diffDays = dayjs().startOf('day').diff(parsed.startOf('day'), 'day')
  if (diffDays <= 7) return '前 7 天'
  if (diffDays <= 30) return '前 30 天'
  return '更早'
}

const formatResultDate = (value) => {
  const parsed = parseToShanghai(value)
  if (!parsed) return ''
  if (parsed.year() === dayjs().year()) return parsed.format('M月D日')
  return parsed.format('YYYY-MM-DD')
}

const splitSnippet = (item) => {
  const content = item?.snippets?.[0]?.content || ''
  const query = trimmedSearchText.value
  if (!content || !query) return [{ text: content, match: false }]

  const lowerContent = content.toLowerCase()
  const lowerQuery = query.toLowerCase()
  const parts = []
  let cursor = 0
  let index = lowerContent.indexOf(lowerQuery)

  while (index >= 0) {
    if (index > cursor) {
      parts.push({ text: content.slice(cursor, index), match: false })
    }
    parts.push({ text: content.slice(index, index + query.length), match: true })
    cursor = index + query.length
    index = lowerContent.indexOf(lowerQuery, cursor)
  }
  if (cursor < content.length) {
    parts.push({ text: content.slice(cursor), match: false })
  }
  return parts
}

// 名称中命中关键词的部分高亮展示
const splitName = (item) => {
  const name = item?.name || ''
  const query = trimmedSearchText.value
  if (!query) return [{ text: name, match: false }]

  const lowerName = name.toLowerCase()
  const lowerQuery = query.toLowerCase()
  const parts = []
  let cursor = 0
  let matchIndex = lowerName.indexOf(lowerQuery)
  while (matchIndex >= 0) {
    if (matchIndex > cursor) parts.push({ text: name.slice(cursor, matchIndex), match: false })
    parts.push({ text: name.slice(matchIndex, matchIndex + query.length), match: true })
    cursor = matchIndex + query.length
    matchIndex = lowerName.indexOf(lowerQuery, cursor)
  }
  if (cursor < name.length) parts.push({ text: name.slice(cursor), match: false })
  return parts
}

watch(
  () => props.open,
  (nextOpen) => {
    if (!nextOpen) return
    activeMode.value = props.defaultMode
    resetState()
    nextTick(() => {
      searchInputRef.value?.focus()
    })
  }
)

watch(trimmedSearchText, (query) => {
  selectedIndex.value = 0
  if (activeMode.value === 'file') {
    clearTimeout(fileSearchTimer)
    fileResults.value = []
    fileSearchError.value = ''
    if (!query) {
      fileSearchRequestId += 1
      isSearching.value = false
      return
    }
    fileSearchTimer = setTimeout(() => searchFiles(query), 250)
    return
  }

  clearTimeout(searchTimer)
  conversationResults.value = []
  hasMore.value = false
  if (!query) {
    searchRequestId += 1
    isSearching.value = false
    return
  }
  isSearching.value = true
  searchTimer = setTimeout(() => {
    searchConversations({ reset: true })
  }, 240)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  clearTimeout(fileSearchTimer)
  searchRequestId += 1
  fileSearchRequestId += 1
})
</script>

<style lang="less" scoped>
.global-search-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 18vh 16px 24px;
  background: color-mix(in srgb, var(--gray-0) 72%, transparent);
  backdrop-filter: blur(2px);
}

.search-modal-enter-active,
.search-modal-leave-active {
  transition: opacity 180ms ease;
}

.search-modal-enter-active .global-search-modal,
.search-modal-leave-active .global-search-modal {
  transition:
    opacity 220ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 260ms cubic-bezier(0.22, 1, 0.36, 1);
  will-change: opacity, transform;
}

.search-modal-enter-from,
.search-modal-leave-to {
  opacity: 0;
}

.search-modal-enter-from .global-search-modal {
  opacity: 0;
  transform: translateY(-10px) scale(0.985);
}

.search-modal-leave-to .global-search-modal {
  opacity: 0;
  transform: translateY(-4px) scale(0.99);
}

.global-search-modal {
  width: min(680px, calc(100vw - 32px));
  max-height: min(620px, 72vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-0);
  box-shadow:
    0 24px 60px var(--shadow-1),
    0 2px 12px var(--shadow-0);
}

.global-search-mode-tabs {
  display: flex;
  gap: 4px;
  padding: 10px 14px 0;
  border-bottom: 1px solid var(--gray-100);
}

.global-search-mode-tab {
  height: 30px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-500);
  font-size: 14px;
  line-height: 20px;
  cursor: pointer;
  transition:
    background-color 0.18s ease,
    color 0.18s ease;

  &:hover,
  &:focus-visible {
    background: var(--gray-50);
    color: var(--gray-900);
    outline: none;
  }

  &.active {
    background: color-mix(in srgb, var(--main-color) 10%, transparent);
    color: var(--main-700);
    font-weight: 600;
  }
}

.global-search-input-row {
  display: flex;
  align-items: center;
  min-height: 62px;
  border-bottom: 1px solid var(--gray-100);
}

.global-search-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 62px;
  padding: 0 18px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--gray-1000);
  font-size: 18px;
  line-height: 24px;

  &::placeholder {
    color: var(--gray-400);
  }
}

.global-search-close {
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
  margin-right: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;

  &:hover,
  &:focus-visible {
    background: var(--gray-50);
    color: var(--gray-900);
    outline: none;
  }
}

.global-search-body {
  min-height: 280px;
  max-height: calc(72vh - 63px);
  overflow-y: auto;
  padding: 8px;
  scrollbar-width: thin;
}

.global-search-default-item,
.global-search-result {
  width: 100%;
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--gray-900);
  cursor: pointer;
  text-align: left;
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease;

  &:hover,
  &.selected,
  &:focus-visible {
    background: var(--gray-50);
    outline: none;
  }
}

.global-search-default-item {
  height: 44px;
  padding: 0 14px;
  font-size: 15px;
}

.default-icon,
.result-icon {
  flex: 0 0 18px;
  color: var(--gray-700);
}

.global-search-group-label {
  padding: 14px 14px 8px;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 18px;
}

.global-search-result {
  min-height: 60px;
  padding: 9px 12px;
}

.result-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.result-title {
  overflow: hidden;
  color: var(--gray-1000);
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-snippet {
  overflow: hidden;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;

  mark {
    padding: 0;
    background: color-mix(in srgb, var(--main-color) 14%, transparent);
    color: var(--main-700);
  }
}

.result-path {
  overflow: hidden;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-date {
  flex: 0 0 auto;
  align-self: center;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 18px;
}

.global-search-skeleton {
  padding: 8px 14px;
}

.skeleton-row {
  height: 58px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.skeleton-dot {
  flex: 0 0 16px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--gray-100);
}

.skeleton-lines {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 10px;

  i {
    height: 8px;
    border-radius: 999px;
    background: var(--gray-100);

    &:first-child {
      width: 190px;
    }

    &:last-child {
      width: min(390px, 72%);
    }
  }
}

.global-search-empty {
  padding: 48px 16px;
  color: var(--gray-500);
  font-size: 14px;
  text-align: center;
}

.global-search-error {
  padding: 48px 16px;
  color: var(--color-error-500, #ff4d4f);
  font-size: 14px;
  text-align: center;
}

.default-empty {
  padding-top: 32px;
}

.global-search-loading-more {
  padding: 10px 0 6px;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

@media (max-width: 640px) {
  .global-search-overlay {
    padding-top: 12vh;
  }

  .global-search-input {
    font-size: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .search-modal-enter-active,
  .search-modal-leave-active,
  .search-modal-enter-active .global-search-modal,
  .search-modal-leave-active .global-search-modal {
    transition-duration: 1ms;
  }
}

.global-search-mode-bar {
  display: flex;
  gap: 14px;
  padding: 4px 18px 10px;
}

.global-search-mode-option {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--gray-500);
  font-size: 13px;
  letter-spacing: 0.01em;
  cursor: pointer;
  transition: color 160ms ease;

  &:hover {
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: none;
    color: var(--gray-900);
  }

  &.active {
    color: var(--gray-1000);
    font-weight: 600;

    &::after {
      content: '';
      position: absolute;
      left: 0;
      right: 0;
      bottom: -6px;
      height: 2px;
      border-radius: 2px;
      background: var(--gray-1000);
    }
  }
}

.mode-icon {
  flex: 0 0 auto;
  opacity: 0.75;
}

.global-search-mode-option.active .mode-icon {
  opacity: 1;
}
</style>
