<template>
  <div class="workspace-overview">
    <PageHeader title="空间概览" show-border
      ><template #actions><slot name="actions" /></template
    ></PageHeader>
    <div class="overview-sections">
      <section
        v-for="section in sections"
        :key="section.key"
        :aria-labelledby="'recent-' + section.key"
      >
        <h2 :id="'recent-' + section.key">{{ section.title }}</h2>
        <div v-if="section.loading && !section.rows.length" class="state" role="status">
          加载中…
        </div>
        <div v-else-if="section.error" class="state" role="alert">
          {{ section.error
          }}<a-button type="link" size="small" @click="load(section)">重试</a-button>
        </div>
        <p v-else-if="!section.rows.length" class="state">{{ section.empty }}</p>
        <table v-else>
          <thead>
            <tr>
              <th>名称</th>
              <th>{{ section.locationLabel }}</th>
              <th>{{ section.timeLabel }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in section.rows" :key="JSON.stringify([entry.thread_id, entry.path])">
              <td>
                <button class="entry" :title="entry.name" @click="$emit(section.event, entry)">
                  <FileTypeIcon :name="entry.name" :size="18" /><span>{{ entry.name }}</span>
                </button>
              </td>
              <td v-if="section.key === 'files'">
                <button
                  class="location"
                  :title="parentPath(entry.path)"
                  @click="$emit('open-directory', parentPath(entry.path))"
                >
                  {{ parentPath(entry.path) === '/' ? '全部文件' : parentPath(entry.path) }}
                </button>
              </td>
              <td v-else :title="entry.thread_title">{{ entry.thread_title || '未命名会话' }}</td>
              <td>
                <time :datetime="entry[section.timeKey]">{{
                  formatRelativeTime(entry[section.timeKey])
                }}</time>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive } from 'vue'
import { threadApi } from '@/apis/agent_api'
import { getRecentWorkspaceFiles } from '@/apis/workspace_api'
import { formatRelativeTime } from '@/shared/lib/file_utils'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'

defineEmits(['open-file', 'open-artifact', 'open-directory'])

const sections = reactive([
  {
    key: 'files',
    title: '最近文件',
    empty: '暂无最近打开的文件',
    event: 'open-file',
    locationLabel: '所在位置',
    timeLabel: '上次打开',
    timeKey: 'opened_at',
    fetchRows: async () => (await getRecentWorkspaceFiles()).entries,
    rows: [],
    loading: true,
    error: ''
  },
  {
    key: 'artifacts',
    title: '最近 Agent 产物',
    empty: '暂无 Agent 产物',
    event: 'open-artifact',
    locationLabel: '来源会话',
    timeLabel: '交付时间',
    timeKey: 'presented_at',
    fetchRows: async () => (await threadApi.getRecentArtifacts()).items,
    rows: [],
    loading: true,
    error: ''
  }
])

const parentPath = (path) => path.slice(0, path.lastIndexOf('/')) || '/'

/** 每个列表独立呈现请求结果，失败时保留另一列表的可用内容。 */
async function load(section) {
  const request = Symbol()
  section.request = request
  section.loading = true
  section.error = ''
  try {
    const rows = await section.fetchRows()
    if (section.request === request) section.rows = rows
  } catch (error) {
    if (section.request === request) section.error = error?.message || '加载失败'
  } finally {
    if (section.request === request) section.loading = false
  }
}
const refresh = () => Promise.all(sections.map(load))
onMounted(refresh)
defineExpose({ refresh })
</script>

<style scoped lang="less">
.workspace-overview {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.overview-sections {
  overflow: auto;
  padding: 24px var(--workspace-content-inset) 32px;
}
section + section {
  margin-top: 24px;
}
h2 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}
table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  text-align: left;
}
th,
td {
  padding: 8px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
th {
  color: var(--gray-600);
  border-bottom: 1px solid var(--gray-150);
  font-size: 12px;
  font-weight: 500;
}
th:first-child {
  width: 54%;
}
th:last-child {
  width: 18%;
}
td {
  border-bottom: 1px solid var(--gray-100);
  color: var(--color-text-secondary);
  font-size: 13px;
  height: 41px;
}
button.entry,
button.location {
  padding: 3px 0;
  border: 0;
  background: transparent;
  text-align: left;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
button.entry {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  color: var(--color-text);
}
.entry span,
.location {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entry :deep(.file-type-icon) {
  flex: none;
}
button:hover {
  color: var(--main-color);
}
.state {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 56px;
  padding: 8px 10px;
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
@container (max-width: 480px) {
  th:first-child {
    width: 48%;
  }
  th:last-child {
    width: 22%;
  }
  th,
  td {
    padding-inline: 4px;
  }
}
</style>
