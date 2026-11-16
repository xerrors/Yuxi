<template>
  <div class="query-section" :class="{ collapsed: !visible }" :style="style">
    <div class="query-section-layout">
      <!-- 主内容区域 -->
      <div class="query-main">
        <div class="query-input-container">
          <div class="search-input-wrapper">
            <a-textarea
              v-model:value="queryText"
              placeholder="输入查询内容..."
              :auto-size="{ minRows: 2, maxRows: 6 }"
              class="search-textarea"
              @press-enter.prevent="onQuery"
            />
            <div class="search-actions">
              <span class="query-hint">Enter 检索知识库内容</span>
              <div style="display: flex; gap: 12px; align-items: center">
                <a-tooltip :title="showRawData ? '切换至格式化显示' : '切换至原始数据'">
                  <a-button
                    type="text"
                    shape="circle"
                    @click="showRawData = !showRawData"
                    class="format-toggle-btn"
                    :class="{ active: showRawData }"
                  >
                    <template #icon><Braces :size="18" /></template>
                  </a-button>
                </a-tooltip>
                <a-button
                  @click="onQuery"
                  :loading="searchLoading"
                  class="search-button"
                  type="primary"
                  :disabled="!queryText.trim()"
                  :icon="h(SearchOutlined)"
                  shape="circle"
                />
              </div>
            </div>
          </div>
        </div>

        <div class="query-results" v-if="queryResult">
          <!-- 原始数据显示 -->
          <div v-if="showRawData" class="result-raw">
            <pre>{{ JSON.stringify(queryResult, null, 2) }}</pre>
          </div>

          <!-- 格式化显示 -->
          <div v-else>
            <div v-if="typeof queryResult === 'string'" class="result-text">
              {{ queryResult }}
            </div>

            <!-- Milvus 返回列表格式 -->
            <div v-else-if="Array.isArray(queryResult)" class="result-list">
              <div v-if="queryResult.length === 0" class="no-results">
                <p>未找到相关结果</p>
              </div>
              <div v-else>
                <div class="result-summary">
                  <span>检索到 {{ queryResult.length }} 个相关文档块：</span>
                  <a-button
                    type="text"
                    size="small"
                    class="clear-results-btn"
                    @click="clearQueryResult"
                  >
                    清空
                  </a-button>
                </div>
                <div v-for="(chunk, index) in queryResult" :key="index" class="result-item">
                  <div class="result-header">
                    <span class="result-index">#{{ index + 1 }}</span>
                    <span v-if="chunk.score" class="result-score">
                      相似度: {{ (chunk.score * 100).toFixed(2) }}%
                    </span>
                    <span v-if="chunk.rerank_score" class="result-rerank-score">
                      重排序分数: {{ (chunk.rerank_score * 100).toFixed(2) }}%
                    </span>
                  </div>

                  <div class="result-content">
                    {{ chunk.content }}
                  </div>

                  <div v-if="chunk.metadata?.result_type === 'parent_child_parent' && chunk.child_hits?.length" class="parent-child-hits">
                    <div class="parent-child-hits-title">子块命中</div>
                    <div v-for="child in chunk.child_hits" :key="child.child_id" class="parent-child-hit">
                      <span class="parent-child-hit-id">{{ child.child_id }}</span>
                      <span v-if="child.chunk_index !== undefined" class="parent-child-hit-index">#{{ child.chunk_index }}</span>
                      <span v-if="child.score !== undefined" class="parent-child-hit-score">{{ Number(child.score).toFixed(4) }}</span>
                      <span v-if="child.start_offset !== undefined && child.end_offset !== undefined" class="parent-child-hit-offset">
                        {{ child.start_offset }}-{{ child.end_offset }}
                      </span>
                    </div>
                  </div>

                  <div class="result-metadata">
                    <span v-if="chunk.metadata?.source" class="metadata-item">
                      <strong>来源:</strong> {{ chunk.metadata.source }}
                    </span>
                    <span v-if="chunk.metadata?.file_id" class="metadata-item">
                      <strong>文件ID:</strong> {{ chunk.metadata.file_id }}
                    </span>
                    <span v-if="chunk.metadata?.chunk_index !== undefined" class="metadata-item">
                      <strong>块索引:</strong> {{ chunk.metadata.chunk_index }}
                    </span>
                    <span v-if="chunk.distance !== undefined" class="metadata-item">
                      <strong>距离:</strong> {{ chunk.distance.toFixed(4) }}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 其他格式（降级处理） -->
            <div v-else class="result-unknown">
              <pre>{{ JSON.stringify(queryResult, null, 2) }}</pre>
            </div>
          </div>
          <!-- 关闭格式化显示的div -->
        </div>

        <div v-else-if="showQuerySuggestions" class="query-suggestions">
          <div v-if="loadingQuestions || generatingQuestions" class="suggestions-loading">
            <a-spin size="small" />
            <span>{{ generatingQuestions ? '正在生成示例问题...' : '正在加载示例问题...' }}</span>
          </div>

          <div v-else-if="queryExamples.length > 0" class="suggestions-list">
            <div class="suggestions-title">示例问题</div>
            <button
              v-for="(example, index) in visibleQueryExamples"
              :key="`${index}-${example}`"
              type="button"
              class="suggestion-row"
              @click="useQueryExample(example)"
            >
              <SearchOutlined class="suggestion-icon" />
              <span class="suggestion-text">{{ example }}</span>
            </button>
            <button
              v-if="canGenerateQuestions"
              type="button"
              class="suggestion-row"
              @click="() => generateSampleQuestions(false)"
            >
              <RefreshCw class="suggestion-icon" />
              <span class="suggestion-text">重新生成</span>
            </button>
          </div>

          <div v-else-if="canGenerateQuestions" class="suggestions-empty">
            <button class="suggestion-row" @click="() => generateSampleQuestions(false)">
              <RefreshCw class="suggestion-icon" />
              <span class="suggestion-text">生成示例问题</span>
            </button>
          </div>
          <div v-else class="suggestions-empty">
            <span class="suggestion-text">暂无示例问题</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, h } from 'vue'
import { useDatabaseStore } from '@/stores/database'
import { message } from 'ant-design-vue'
import { queryApi } from '@/apis/knowledge_api'
import { SearchOutlined } from '@ant-design/icons-vue'
import { Braces, RefreshCw } from '@lucide/vue'

const store = useDatabaseStore()
const MAX_VISIBLE_EXAMPLES = 10

defineProps({
  visible: {
    type: Boolean,
    default: true
  },
  style: {
    type: Object,
    default: () => ({})
  }
})

// 声明事件
defineEmits(['toggleVisible'])

const searchLoading = computed(() => store.state.searchLoading)
const queryResult = ref('')
const showRawData = ref(false)
const showQuerySuggestions = computed(() => !searchLoading.value && !queryResult.value)

// 示例问题生成属于写操作，仅对拥有管理权限（非只读权限）的知识库开放
const canGenerateQuestions = computed(() => store.database?.can_manage === true)

// 查询测试
const queryText = ref('')

// 示例问题相关
const queryExamples = ref([])
const visibleQueryExamples = ref([])
const loadingQuestions = ref(false)
const generatingQuestions = ref(false)

const updateQueryExamples = (questions = []) => {
  queryExamples.value = questions
  const shuffledQuestions = [...questions]
  for (let index = shuffledQuestions.length - 1; index > 0; index--) {
    const targetIndex = Math.floor(Math.random() * (index + 1))
    const currentQuestion = shuffledQuestions[index]
    shuffledQuestions[index] = shuffledQuestions[targetIndex]
    shuffledQuestions[targetIndex] = currentQuestion
  }
  visibleQueryExamples.value = shuffledQuestions.slice(0, MAX_VISIBLE_EXAMPLES)
}

// 加载示例问题
const loadSampleQuestions = async () => {
  if (!store.database?.kb_id) return

  try {
    loadingQuestions.value = true
    const data = await queryApi.getSampleQuestions(store.database.kb_id)
    if (data.questions && data.questions.length > 0) {
      updateQueryExamples(data.questions)
    } else {
      // 如果没有问题，清空列表
      updateQueryExamples()
    }
  } catch (error) {
    // 404表示还没有生成问题，清空问题列表
    if (
      error.status === 404 ||
      error?.message?.includes('404') ||
      error?.message?.includes('还没有生成')
    ) {
      updateQueryExamples()
    } else {
      console.error('加载示例问题失败:', error)
    }
  } finally {
    loadingQuestions.value = false
  }
}

// 清空问题列表
const clearQuestions = () => {
  updateQueryExamples()
}

// 生成示例问题
const generateSampleQuestions = async (silent = false) => {
  if (!store.database?.kb_id) return

  try {
    generatingQuestions.value = true
    const data = await queryApi.generateSampleQuestions(store.database.kb_id, 10)
    if (data.questions && data.questions.length > 0) {
      updateQueryExamples(data.questions)
      if (!silent) {
        message.success(`成功生成 ${data.questions.length} 个测试问题`)
      }
    }
  } catch (error) {
    console.error('生成示例问题失败:', error)
    // 静默模式下不显示错误消息（自动生成时）
    if (!silent) {
      // 提取详细错误信息
      let errorMsg
      if (error.response?.data?.detail) {
        errorMsg = error.response.data.detail
      } else if (error.detail) {
        errorMsg = error.detail
      } else if (error.message) {
        errorMsg = error.message
      } else if (typeof error === 'string') {
        errorMsg = error
      } else {
        errorMsg = JSON.stringify(error)
      }
      message.error('生成失败: ' + errorMsg)
    }
  } finally {
    generatingQuestions.value = false
  }
}

const useQueryExample = (example) => {
  queryText.value = example
  onQuery()
}

const clearQueryResult = () => {
  queryResult.value = ''
}

// 监听知识库ID变化，切换知识库时重新加载问题
watch(
  () => store.database?.kb_id,
  async (newKbId, oldKbId) => {
    // 如果知识库ID发生变化
    if (newKbId && newKbId !== oldKbId) {
      // 清空当前问题列表
      updateQueryExamples()
      // 重新加载新知识库的问题
      await loadSampleQuestions()
    }
  },
  { immediate: false }
)

const onQuery = async () => {
  if (!queryText.value.trim()) {
    message.error('请输入查询内容')
    return
  }

  store.state.searchLoading = true

  // 从store中获取配置参数
  const queryMeta = { ...store.meta }

  try {
    const data = await queryApi.queryTest(store.database.kb_id, queryText.value.trim(), queryMeta)
    queryResult.value = data
  } catch (error) {
    console.error(error)
    message.error(error.message)
    queryResult.value = ''
  } finally {
    store.state.searchLoading = false
  }
}

// 组件挂载时启动示例轮播
onMounted(async () => {
  // 加载查询参数
  store.loadQueryParams()

  // 加载示例问题
  await loadSampleQuestions()
  // 不自动生成，只在创建知识库和添加文件时由 DataBaseInfoView 触发生成
})

// 检查是否已有问题
const hasQuestions = () => {
  return queryExamples.value.length > 0
}

// 暴露给父组件的方法和属性
defineExpose({
  generateSampleQuestions,
  loadSampleQuestions,
  hasQuestions,
  clearQuestions,
  queryExamples
})
</script>

<style scoped lang="less">
.query-section {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.query-section-layout {
  height: 100%;
  overflow: hidden;
}

// 主内容区域
.query-main {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  max-height: 100%;
}

.query-input-container {
  padding-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.search-input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px 8px;
  border-radius: 12px;
  border: 1px solid var(--gray-200);
  background-color: var(--gray-0);
  box-shadow: 0 1px 3px var(--shadow-1);
  transition:
    border-color 0.5s ease,
    box-shadow 0.5s ease;

  &:hover {
    border-color: var(--main-400);
    box-shadow: 0 4px 12px var(--shadow-2);
  }

  :deep(.ant-input) {
    border-radius: 8px;
    background-color: var(--gray-0);
    color: var(--gray-1000);
    outline: none;
    border: none;
    box-shadow: none;
    padding: 0;
    transition:
      border-color 0.3s ease,
      box-shadow 0.3s ease;

    &:focus {
      outline: none;
      border: none;
    }

    &::placeholder {
      color: var(--gray-500);
    }
  }
}

.search-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.search-button {
  background-color: var(--main-color);
  border-color: var(--main-color);
  box-shadow: 0 2px 4px var(--shadow-3);
  transition: all 0.2s ease;

  &:hover {
    background-color: var(--main-bright);
    border-color: var(--main-bright);
    box-shadow: 0 4px 8px rgba(1, 136, 166, 0.25);
    transform: translateY(-1px);
  }

  &:disabled {
    opacity: 0.5;
    color: var(--gray-0);
    cursor: not-allowed;
    box-shadow: none;
    transform: none;
  }
}

.format-toggle-btn {
  color: var(--gray-500);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;

  &:hover {
    color: var(--main-color);
    background-color: var(--main-50);
  }

  &.active {
    color: var(--main-color);
    background-color: var(--main-50);
  }
}

.query-results {
  flex: 1;
  overflow-y: auto;
  background-color: var(--gray-25);
  min-height: 0;

  .result-raw {
    padding: 16px;
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    overflow-x: auto;

    pre {
      margin: 0;
      font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
      font-size: 12px;
      line-height: 1.5;
      color: var(--gray-1000);
      white-space: pre-wrap;
      word-break: break-word;
    }
  }

  .result-text {
    padding: 16px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
    color: var(--gray-1000);
  }

  .result-list {
    // padding: 16px;

    .no-results {
      text-align: center;
      padding: 32px;
      color: var(--gray-500);
    }

    .result-summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
      padding: 10px 14px;
      background-color: var(--main-50);
      border-radius: 6px;
      color: var(--gray-800);
      font-size: 13px;
      span {
        font-weight: 500;
      }
    }

    .clear-results-btn {
      flex: 0 0 auto;
      color: var(--main-color);
      background-color: var(--main-100);
      border-radius: 6px;

      &:hover {
        color: var(--main-800);
        background-color: var(--main-200);
      }
    }

    .result-item {
      background-color: var(--gray-0);
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      padding: 12px;
      margin-bottom: 12px;
      transition: all 0.2s ease;

      &:hover {
        border-color: var(--main-300);
        box-shadow: 0 2px 8px rgba(1, 97, 121, 0.08);
      }

      &:last-child {
        margin-bottom: 0;
      }

      .result-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 8px;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--gray-150);

        .result-index {
          font-weight: 600;
          color: var(--main-color);
          font-size: 14px;
        }

        .result-score,
        .result-rerank-score {
          font-size: 12px;
          padding: 2px 8px;
          border-radius: 12px;
          background-color: var(--gray-100);
          color: var(--gray-700);
        }

        .result-rerank-score {
          background-color: var(--color-warning-50);
          color: var(--color-warning-700);
        }
      }

      .result-content {
        padding: 8px 0;
        line-height: 1.6;
        font-size: 13px;
        color: var(--gray-900);
        white-space: pre-wrap;
        word-break: break-word;
      }

      .result-metadata {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--gray-150);

        .metadata-item {
          font-size: 12px;
          color: var(--gray-700);

          strong {
            color: var(--gray-500);
            font-weight: 500;
            margin-right: 4px;
          }
        }
      }

      .parent-child-hits {
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--gray-150);
      }

      .parent-child-hits-title {
        margin-bottom: 6px;
        color: var(--gray-600);
        font-size: 12px;
        font-weight: 600;
      }

      .parent-child-hit {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        min-height: 24px;
        color: var(--gray-700);
        font-size: 12px;
      }

      .parent-child-hit-id {
        color: var(--main-700);
        font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
      }

      .parent-child-hit-index,
      .parent-child-hit-score,
      .parent-child-hit-offset {
        padding: 1px 6px;
        border-radius: 4px;
        background: var(--gray-100);
      }
    }
  }

  .result-unknown {
    padding: 16px;

    pre {
      background-color: var(--gray-0);
      border: 1px solid var(--gray-200);
      padding: 12px;
      border-radius: 4px;
      overflow-x: auto;
      font-size: 12px;
      color: var(--gray-1000);
    }
  }
}

.query-hint {
  font-size: 12px;
  color: var(--gray-500);
  line-height: 24px;
}

.query-suggestions {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 0 16px;
}

.suggestions-loading,
.suggestions-empty {
  min-height: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--gray-500);
  font-size: 13px;
}

.suggestions-loading {
  gap: 6px;
}

.suggestions-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
}

.suggestions-title {
  padding: 0 2px;
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
}

.suggestion-row {
  width: fit-content;
  max-width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border: none;
  border-radius: 30px;
  background-color: var(--gray-0);
  color: var(--gray-800);
  text-align: left;
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    box-shadow 0.2s ease;

  &:hover {
    box-shadow: 0 2px 8px var(--shadow-1);

    .suggestion-icon {
      color: var(--main-800);
      opacity: 1;
    }
  }

  &:active {
    background-color: var(--main-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 2px;
  }
}

.suggestion-text {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.suggestion-icon {
  flex: 0 0 auto;
  color: var(--main-color);
  font-size: 14px;
  width: 14px;
  height: 14px;
  opacity: 0.82;
  transition:
    color 0.2s ease,
    opacity 0.2s ease;
}

.generate-suggestions-btn {
  height: auto;
  background-color: var(--gray-0);
  border-radius: 40px;

  &:hover {
    background-color: var(--gray-0);
    box-shadow: 0 2px 8px var(--shadow-1);
  }
}

@media (max-width: 767px) {
  .query-hint {
    display: none;
  }

  .suggestion-row {
    align-items: flex-start;
  }

  .suggestion-icon {
    margin-top: 3px;
  }
}
</style>
