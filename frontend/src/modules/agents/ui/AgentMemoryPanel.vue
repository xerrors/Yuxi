<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { Copy, FilePen, RefreshCw } from '@lucide/vue'
import { getWorkspaceFileContent, saveWorkspaceFileContent } from '@/apis/workspace_api'
import MarkdownPreview from '@/modules/workspace/ui/MarkdownPreview.vue'
import { normalizePreviewResponse } from '@/shared/lib/file_preview'

const props = defineProps({ disabled: { type: Boolean, default: false } })
const emit = defineEmits(['busy'])
const files = [
  {
    key: 'memory',
    label: '长期记忆',
    path: '/agents/MEMORY.md',
    description: '需要在后续会话中记住的信息。自动使用由账户设置中的 Memory 开关控制。'
  },
  {
    key: 'user',
    label: '用户档案',
    path: '/agents/USER.md',
    description: '你的偏好、背景和其他希望智能体了解的信息。'
  },
  {
    key: 'agents',
    label: '行为约定',
    path: '/agents/AGENTS.md',
    description: '你希望智能体遵循的工作方式和行为要求。'
  }
]
const selectedKey = ref('memory')
const selectedFile = computed(() => files.find((file) => file.key === selectedKey.value))
const content = ref('')
const draft = ref('')
const editing = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const dirty = computed(() => editing.value && draft.value !== content.value)
const locked = computed(() => props.disabled || saving.value)
let loadRevision = 0
let leaveRequest = null

/** 读取完整文本预览，拒绝将不支持的预览结果当成可编辑内容。 */
async function readContent(path) {
  const response = await getWorkspaceFileContent(path)
  const preview = await normalizePreviewResponse(response, { path })
  if (preview.truncated) throw new Error('文件预览已截断，无法编辑，请先在网盘中整理源文件')
  if (
    !preview.supported ||
    !['markdown', 'text'].includes(preview.previewType) ||
    typeof preview.content !== 'string'
  ) {
    throw new Error(preview.message || '此文件暂不支持编辑，请在网盘中查看')
  }
  return preview.content
}

/** 重新读取所选文件，忽略切换后到达的旧响应。 */
async function load() {
  const revision = ++loadRevision
  const path = selectedFile.value.path
  loading.value = true
  error.value = ''
  try {
    const next = await readContent(path)
    if (revision !== loadRevision) return
    content.value = next
  } catch (cause) {
    if (revision === loadRevision) error.value = cause.message || '记忆加载失败，请重试'
  } finally {
    if (revision === loadRevision) loading.value = false
  }
}

/** 放弃当前编辑并恢复已读取的内容。 */
function cancelEdit() {
  editing.value = false
  draft.value = ''
}

/** 关闭、刷新和切换前统一确认草稿，保存期间阻止离开。 */
async function confirmLeave() {
  if (saving.value) return false
  if (!dirty.value) {
    cancelEdit()
    return true
  }
  if (!leaveRequest) {
    leaveRequest = new Promise((resolve) => {
      Modal.confirm({
        title: '放弃未保存的修改？',
        content: '记忆内容尚未保存，离开后这些修改将丢失。',
        okText: '放弃修改',
        cancelText: '继续编辑',
        onOk: () => {
          cancelEdit()
          resolve(true)
        },
        onCancel: () => resolve(false)
      })
    }).finally(() => {
      leaveRequest = null
    })
  }
  return leaveRequest
}

/** 切换文件前处理未保存草稿。 */
async function selectFile(key) {
  if (key === selectedKey.value || !(await confirmLeave())) return
  selectedKey.value = key
  content.value = ''
  await load()
}

/** 刷新前处理未保存草稿。 */
async function refresh() {
  if (await confirmLeave()) await load()
}

/** 从当前完整文件开始编辑。 */
function startEdit() {
  if (locked.value || loading.value || error.value) return
  draft.value = content.value
  editing.value = true
}

/** 独立保存个人文件并回读服务端内容；失败时保留草稿。 */
async function save() {
  if (!dirty.value || locked.value) return
  saving.value = true
  emit('busy', true)
  try {
    const path = selectedFile.value.path
    await saveWorkspaceFileContent(path, draft.value)
    content.value = await readContent(path)
    cancelEdit()
    message.success('记忆文件已保存')
  } catch (cause) {
    message.error(cause.message || '保存失败，请重试')
  } finally {
    saving.value = false
    emit('busy', false)
  }
}

/** 复制当前已读取的文件内容。 */
async function copyContent() {
  try {
    await navigator.clipboard.writeText(content.value)
    message.success('内容已复制')
  } catch {
    message.error('复制失败，请手动选择内容复制')
  }
}

onMounted(load)
onBeforeUnmount(() => {
  loadRevision += 1
})
defineExpose({ confirmLeave })
</script>

<template>
  <section class="agent-memory-panel" aria-label="个人记忆">
    <div class="memory-intro">
      <h3>个人记忆</h3>
      <p>仅属于当前登录用户，在你的不同智能体和会话间共享，不会分享给此智能体的其他使用者。</p>
    </div>
    <a-tabs :active-key="selectedKey" size="small" @change="selectFile">
      <a-tab-pane v-for="file in files" :key="file.key" :tab="file.label" :disabled="locked" />
    </a-tabs>
    <div class="memory-file-header">
      <div class="memory-file-meta">
        <code>{{ selectedFile.path }}</code>
        <p>{{ selectedFile.description }}</p>
      </div>
      <div class="memory-actions">
        <template v-if="editing">
          <a-button size="small" :disabled="locked" @click="confirmLeave">取消</a-button>
          <a-button
            size="small"
            type="primary"
            :loading="saving"
            :disabled="locked || !dirty"
            @click="save"
            >保存记忆</a-button
          >
        </template>
        <template v-else>
          <a-button
            size="small"
            class="lucide-icon-btn"
            :disabled="locked || loading || !!error"
            @click="startEdit"
            ><FilePen :size="14" />编辑</a-button
          >
          <a-button
            size="small"
            type="text"
            class="lucide-icon-btn"
            aria-label="复制内容"
            :disabled="locked || loading || !!error || !content"
            @click="copyContent"
            ><Copy :size="14"
          /></a-button>
        </template>
        <a-button
          size="small"
          type="text"
          class="lucide-icon-btn"
          aria-label="刷新记忆"
          :disabled="loading || locked"
          @click="refresh"
          ><RefreshCw :size="14"
        /></a-button>
      </div>
    </div>
    <div v-if="loading" class="memory-state" role="status">
      <a-spin size="small" /><span>正在读取记忆…</span>
    </div>
    <div v-else-if="error" class="memory-state" role="alert">
      <span>{{ error }}</span>
      <a-button size="small" @click="load">重试</a-button>
    </div>
    <textarea
      v-else-if="editing"
      v-model="draft"
      class="memory-editor"
      :aria-label="`编辑${selectedFile.label}`"
      :readonly="locked"
      spellcheck="false"
    />
    <div v-else-if="!content.trim()" class="memory-state">
      <span>这里还没有内容</span>
      <a-button size="small" :disabled="locked" @click="startEdit">添加内容</a-button>
    </div>
    <div v-else class="memory-preview"><MarkdownPreview :content="content" compact /></div>
    <p class="memory-footer">
      {{ dirty ? '有未保存的修改 · ' : '' }}记忆文件独立保存，不随智能体配置一起保存。
    </p>
  </section>
</template>

<style scoped lang="less">
.agent-memory-panel {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  gap: 12px;
}
.memory-intro {
  h3 {
    margin: 0 0 6px;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
  }
  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.7;
  }
}
:deep(.ant-tabs-nav) {
  margin-bottom: 0;
}
.memory-file-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.memory-file-meta {
  flex: 1 1 200px;
  code {
    color: var(--gray-700);
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  p {
    margin: 4px 0 0;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 1.6;
  }
}
.memory-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 4px;
}
.memory-state {
  display: flex;
  flex: 1;
  min-height: 220px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  color: var(--gray-500);
  font-size: 13px;
}
.memory-preview,
.memory-editor {
  flex: 1;
  min-height: 180px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}
.memory-preview {
  padding: 8px 16px;
  overflow-wrap: anywhere;
  min-width: 0;
  overflow-x: auto;
}
.memory-editor {
  width: 100%;
  padding: 14px 16px;
  resize: vertical;
  color: var(--gray-900);
  font-family: monospace;
  font-size: 13px;
  line-height: 1.8;
  &:focus-visible {
    outline: 2px solid var(--main-100);
    border-color: var(--main-300);
  }
}
.memory-footer {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.6;
}
</style>
