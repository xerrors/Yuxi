<template>
  <Teleport to="body">
    <Transition name="search-modal" appear>
      <div v-if="open" class="file-search-overlay" @mousedown.self="close">
        <section
          class="file-search-modal"
          role="dialog"
          aria-modal="true"
          aria-label="搜索文件"
          @keydown.esc.prevent="close"
        >
          <div class="file-search-input-row">
            <Search :size="18" class="file-search-input-icon" />
            <input
              ref="searchInputRef"
              v-model="keyword"
              class="file-search-input"
              type="text"
              placeholder="输入文件名搜索（仅匹配文件名，不搜索文件内容）"
              autocomplete="off"
              aria-label="搜索文件"
              @keydown.enter.prevent="handleSearch"
            />
            <button type="button" class="file-search-close" aria-label="关闭" @click="close">
              <X :size="20" />
            </button>
          </div>

          <div ref="resultListRef" class="file-search-body">
            <template v-if="hasSearched">
              <div v-if="loading && results.length === 0" class="file-search-skeleton">
                <div v-for="index in 5" :key="index" class="skeleton-row">
                  <span class="skeleton-icon"></span>
                  <span class="skeleton-lines">
                    <i></i>
                    <i></i>
                  </span>
                </div>
              </div>

              <div v-else-if="results.length > 0" class="file-search-results">
                <button
                  v-for="item in results"
                  :key="item.file_id"
                  type="button"
                  class="file-search-result"
                  @click="selectResult(item)"
                >
                  <FileText :size="18" class="result-icon" />
                  <span class="result-main">
                    <span class="result-title" :title="splitFilename(item.filename).basename">
                      {{ splitFilename(item.filename).basename }}
                    </span>
                    <span class="result-meta">
                      <span
                        v-if="splitFilename(item.filename).dirname"
                        class="result-path"
                        :title="item.filename"
                      >
                        {{ formatDirname(splitFilename(item.filename).dirname) }}
                      </span>
                      <span v-if="item.file_size != null" class="result-size">
                        {{ formatFileSize(item.file_size) }}
                      </span>
                      <span class="result-date">{{ formatResultDate(item.updated_at) }}</span>
                    </span>
                  </span>
                </button>
                <div v-if="hasMore" class="file-search-loading-more">
                  仅展示前 {{ results.length }} 条，请细化关键词
                </div>
              </div>

              <div v-else class="file-search-empty">未找到匹配的文件</div>
            </template>

            <div v-else class="file-search-hint">
              输入文件名关键词进行搜索，仅匹配文件名，不搜索文件内容。
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import { FileText, Search, X } from '@lucide/vue'
import dayjs, { parseToShanghai } from '@/utils/time'
import { formatFileSize } from '@/utils/file_utils'
import { documentApi } from '@/apis/knowledge_api'

const SEARCH_LIMIT = 100

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' }
})

const emit = defineEmits(['update:open', 'select'])

const searchInputRef = ref(null)
const resultListRef = ref(null)
const keyword = ref('')
const results = ref([])
const loading = ref(false)
const hasSearched = ref(false)
const hasMore = ref(false)

const resetState = () => {
  searchToken++
  keyword.value = ''
  results.value = []
  loading.value = false
  hasSearched.value = false
  hasMore.value = false
}

const close = () => emit('update:open', false)

const selectResult = (item) => {
  emit('select', item)
  close()
}

let searchToken = 0

const handleSearch = async () => {
  const query = keyword.value.trim()
  if (!query || !props.kbId) return
  const token = ++searchToken
  loading.value = true
  hasSearched.value = true
  try {
    const response = await documentApi.searchDocuments(props.kbId, {
      query,
      offset: 0,
      limit: SEARCH_LIMIT
    })
    if (token !== searchToken) return
    results.value = response?.files || []
    hasMore.value = Boolean(response?.has_more)
  } catch (error) {
    if (token !== searchToken) return
    console.warn('搜索文件失败:', error)
    results.value = []
    hasMore.value = false
  } finally {
    if (token === searchToken) loading.value = false
  }
}

const PATH_PREVIEW_LIMIT = 48

const splitFilename = (filename) => {
  const name = filename || ''
  const idx = name.lastIndexOf('/')
  if (idx < 0) return { basename: name, dirname: '' }
  return { basename: name.slice(idx + 1), dirname: name.slice(0, idx) }
}

const formatDirname = (dir) => {
  if (!dir) return ''
  if (dir.length <= PATH_PREVIEW_LIMIT) return dir
  return '...' + dir.slice(dir.length - PATH_PREVIEW_LIMIT + 3)
}

const formatResultDate = (value) => {
  const parsed = parseToShanghai(value)
  if (!parsed) return ''
  if (parsed.year() === dayjs().year()) return parsed.format('M月D日 HH:mm')
  return parsed.format('YYYY-MM-DD HH:mm')
}

watch(
  () => props.open,
  (nextOpen) => {
    if (!nextOpen) return
    resetState()
    nextTick(() => searchInputRef.value?.focus())
  }
)
</script>

<style lang="less" scoped>
.file-search-overlay {
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

.search-modal-enter-active .file-search-modal,
.search-modal-leave-active .file-search-modal {
  transition:
    opacity 220ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 260ms cubic-bezier(0.22, 1, 0.36, 1);
  will-change: opacity, transform;
}

.search-modal-enter-from,
.search-modal-leave-to {
  opacity: 0;
}

.search-modal-enter-from .file-search-modal {
  opacity: 0;
  transform: translateY(-10px) scale(0.985);
}

.search-modal-leave-to .file-search-modal {
  opacity: 0;
  transform: translateY(-4px) scale(0.99);
}

.file-search-modal {
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

.file-search-input-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 62px;
  padding: 0 10px 0 18px;
  border-bottom: 1px solid var(--gray-100);
}

.file-search-input-icon {
  flex: 0 0 18px;
  color: var(--gray-400);
}

.file-search-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 62px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--gray-1000);
  font-size: 16px;
  line-height: 24px;

  &::placeholder {
    color: var(--gray-400);
  }
}

.file-search-close {
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
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

.file-search-body {
  min-height: 280px;
  max-height: calc(72vh - 63px);
  overflow-y: auto;
  padding: 8px;
  scrollbar-width: thin;
}

.file-search-result {
  width: 100%;
  min-height: 56px;
  padding: 9px 12px;
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
  &:focus-visible {
    background: var(--gray-50);
    outline: none;
  }
}

.result-icon {
  flex: 0 0 18px;
  color: var(--gray-700);
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

.result-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.result-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--gray-500);
}

.result-size {
  flex: 0 0 auto;
  color: var(--gray-500);
}

.result-date {
  flex: 0 0 auto;
  color: var(--gray-500);
}

.file-search-skeleton {
  padding: 8px 14px;
}

.skeleton-row {
  height: 54px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.skeleton-icon {
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
  border-radius: 4px;
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

.file-search-empty,
.file-search-hint {
  padding: 48px 16px;
  color: var(--gray-500);
  font-size: 14px;
  text-align: center;
}

.file-search-loading-more {
  padding: 10px 0 6px;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

@media (max-width: 640px) {
  .file-search-overlay {
    padding-top: 12vh;
  }

  .file-search-input {
    font-size: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .search-modal-enter-active,
  .search-modal-leave-active,
  .search-modal-enter-active .file-search-modal,
  .search-modal-leave-active .file-search-modal {
    transition-duration: 1ms;
  }
}
</style>
