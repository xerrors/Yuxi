<template>
  <div class="tools-manager-container extension-page-root">
    <div v-if="loading" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>
    <div class="layout-wrapper" :class="{ 'content-loading': loading }">
      <!-- 左侧：工具列表 -->
      <div class="sidebar-list">
        <div class="search-box">
          <a-input
            v-model:value="searchQuery"
            placeholder="搜索工具..."
            allow-clear
            class="search-input"
          >
            <template #prefix><Search :size="14" class="text-muted" /></template>
          </a-input>
        </div>
        <!-- 分类筛选 -->
        <div class="category-filter">
          <a-select
            v-model:value="selectedCategory"
            placeholder="全部分类"
            allow-clear
            style="width: 100%"
          >
            <a-select-option value="">全部分类</a-select-option>
            <a-select-option v-for="cat in categories" :key="cat" :value="cat">
              {{ categoryLabels[cat] || cat }}
            </a-select-option>
          </a-select>
        </div>

        <div class="list-container">
          <div v-if="filteredTools.length === 0" class="empty-text">
            <a-empty :image="false" description="无匹配工具" />
          </div>
          <template v-for="(tool, index) in filteredTools" :key="tool.id">
            <div
              class="list-item"
              :class="{ active: currentTool?.id === tool.id }"
              @click="selectTool(tool)"
            >
              <div class="item-header">
                <Wrench :size="16" class="item-icon" />
                <span class="item-name">{{ tool.name }}</span>
              </div>
              <div class="item-details item-details-inline">
                <span class="item-category item-meta">{{
                  categoryLabels[tool.category] || tool.category
                }}</span>
                <div class="item-tags">
                  <a-tag v-for="tag in tool.tags" :key="tag" size="small" class="tool-tag">{{
                    tag
                  }}</a-tag>
                </div>
              </div>
            </div>
            <div v-if="index < filteredTools.length - 1" class="list-separator"></div>
          </template>
        </div>
      </div>

      <!-- 右侧：详情面板 -->
      <div class="main-panel">
        <div v-if="!currentTool" class="unselected-state">
          <div class="hint-box">
            <Wrench :size="40" class="text-muted" />
            <p>请在左侧选择工具查看详情</p>
          </div>
        </div>

        <template v-else>
          <div class="panel-top-bar">
            <div class="panel-title-stack">
              <h2>{{ currentTool.name }}</h2>
              <code class="panel-title-meta">{{ currentTool.id }}</code>
            </div>
          </div>

          <div class="tool-detail">
            <div class="detail-section">
              <div class="section-header">
                <FileText :size="14" />
                <span>描述</span>
              </div>
              <div class="section-content description">
                {{ currentTool.description || '无描述' }}
              </div>
            </div>

            <div class="detail-section">
              <div class="section-header">
                <Tag :size="14" />
                <span>分类</span>
              </div>
              <div class="section-content">
                <a-tag :color="categoryColors[currentTool.category] || 'default'">
                  {{ categoryLabels[currentTool.category] || currentTool.category }}
                </a-tag>
              </div>
            </div>

            <div class="detail-section">
              <div class="section-header">
                <Tags :size="14" />
                <span>标签</span>
              </div>
              <div class="section-content">
                <a-tag v-for="tag in currentTool.tags" :key="tag">{{ tag }}</a-tag>
                <span v-if="!currentTool.tags?.length" class="text-muted">无</span>
              </div>
            </div>

            <div class="detail-section" v-if="currentTool.args?.length">
              <div class="section-header">
                <List :size="14" />
                <span>参数</span>
              </div>
              <div class="section-content">
                <a-table
                  :dataSource="currentTool.args"
                  :columns="argColumns"
                  size="small"
                  :pagination="false"
                  bordered
                  class="args-table"
                />
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Search, Wrench, Tag, Tags, FileText, List } from 'lucide-vue-next'
import { toolApi } from '@/apis/tool_api'

const loading = ref(false)
const searchQuery = ref('')
const selectedCategory = ref('')

const tools = ref([])
const currentTool = ref(null)

const categories = ['buildin', 'mysql', 'debug']
const categoryLabels = {
  buildin: '内置工具',
  mysql: 'MySQL',
  debug: '调试'
}
const categoryColors = {
  buildin: 'blue',
  mysql: 'green',
  debug: 'orange'
}

const argColumns = [
  { title: '参数名', dataIndex: 'name', key: 'name' },
  { title: '类型', dataIndex: 'type', key: 'type', width: 80 },
  { title: '描述', dataIndex: 'description', key: 'description' }
]

const filteredTools = computed(() => {
  let result = tools.value
  if (selectedCategory.value) {
    result = result.filter((t) => t.category === selectedCategory.value)
  }
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q)
    )
  }
  return result
})

const fetchTools = async () => {
  loading.value = true
  try {
    const result = await toolApi.getTools()
    tools.value = result?.data || []
    // 默认选中第一个工具
    if (!currentTool.value && tools.value.length > 0) {
      currentTool.value = tools.value[0]
    }
  } catch (error) {
    message.error('加载工具失败')
  } finally {
    loading.value = false
  }
}

const selectTool = (record) => {
  currentTool.value = record
}

onMounted(fetchTools)

// 暴露方法给父组件
defineExpose({
  fetchTools
})
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.category-filter {
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-150);
}

.list-item {
  .item-details {
    .item-tags {
      display: flex;
      gap: 2px;
      flex-wrap: wrap;
      max-width: 100px;
      justify-content: flex-end;
      .tool-tag {
        color: var(--gray-500);
        font-size: 11px;
        padding: 0 4px;
        margin: 0;
        border: none;
      }
    }
  }
}

.tool-detail {
  padding: 16px;
  flex: 1;
  overflow-y: auto;

  .detail-section {
    margin-bottom: 20px;

    .section-header {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-700);
      margin-bottom: 8px;

      svg {
        color: var(--gray-500);
      }
    }

    .section-content {
      &.description {
        font-size: 13px;
        color: var(--gray-600);
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
      }
    }
  }
}

.args-table {
  :deep(.ant-table) {
    font-size: 12px;
  }
}
</style>
