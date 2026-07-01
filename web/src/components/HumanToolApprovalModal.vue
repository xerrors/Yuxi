<template>
  <transition name="slide-up">
    <div v-if="visible && actionRequests.length" class="approval-modal">
      <div class="approval-content">
        <div class="approval-header">
          <h4>操作需要确认</h4>
          <span v-if="actionRequests.length > 1" class="action-count">
            共 {{ actionRequests.length }} 个操作
          </span>
        </div>

        <div
          v-for="(action, index) in actionRequests"
          :key="index"
          class="action-block"
        >
          <div class="action-meta">
            <span class="action-label">工具：</span>
            <code class="action-name">{{ action.name }}</code>
          </div>
          <div class="action-meta">
            <span class="action-label">参数：</span>
            <code class="action-args">{{ formatArgs(action.args) }}</code>
          </div>
          <p v-if="action.description" class="action-desc">{{ action.description }}</p>

          <div v-if="editingIndex === index" class="edit-area">
            <textarea
              v-model="editText"
              class="edit-textarea"
              :disabled="isProcessing"
              spellcheck="false"
            />
            <p v-if="editError" class="edit-error">{{ editError }}</p>
          </div>
        </div>

        <div v-if="actionRequests.length > 1" class="hint">
          所有操作将使用同一决策，编辑模式会把参数应用到每个操作。
        </div>
      </div>

      <div class="approval-actions">
        <button class="btn btn-reject" :disabled="isProcessing" @click="handleReject">拒绝</button>
        <button
          v-if="canEdit"
          class="btn btn-edit"
          :disabled="isProcessing"
          @click="toggleEdit"
        >
          {{ editingIndex === null ? '编辑参数' : '取消编辑' }}
        </button>
        <button class="btn btn-approve" :disabled="isProcessing" @click="handlePrimary">
          确认执行
        </button>
      </div>

      <div v-if="isProcessing" class="approval-processing">
        <span class="processing-spinner"></span>
        处理中...
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  actionRequests: { type: Array, default: () => [] },
  reviewConfigs: { type: Array, default: () => [] }
})

const emit = defineEmits(['approve', 'reject', 'edit'])

const isProcessing = ref(false)
const editingIndex = ref(null)
const editText = ref('')
const editError = ref('')

const canEdit = computed(() => {
  if (props.actionRequests.length !== 1) return false
  const cfg = props.reviewConfigs[0]
  if (!cfg) return true
  const allowed = cfg.allowed_decisions || cfg.allowedDecisions || []
  return allowed.length === 0 || allowed.includes('edit')
})

const formatArgs = (args) => {
  if (args === null || args === undefined) return ''
  if (typeof args === 'string') return args
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

const toggleEdit = () => {
  if (editingIndex.value === null) {
    const action = props.actionRequests[0]
    editText.value = formatArgs(action?.args)
    editError.value = ''
    editingIndex.value = 0
  } else {
    editingIndex.value = null
    editText.value = ''
    editError.value = ''
  }
}

const parseEditedArgs = () => {
  try {
    const parsed = JSON.parse(editText.value)
    editError.value = ''
    return parsed
  } catch (e) {
    editError.value = `参数不是合法 JSON：${e.message}`
    return null
  }
}

const buildDecisions = (type, extra = {}) => {
  // 每个待审批操作生成一个决策，顺序与 actionRequests 一致
  return props.actionRequests.map((action) => {
    if (type === 'edit') {
      return {
        type: 'edit',
        edited_action: {
          name: action.name,
          args: extra.args
        }
      }
    }
    return { type }
  })
}

const handleApprove = () => {
  if (isProcessing.value) return
  isProcessing.value = true
  emit('approve', { decisions: buildDecisions('approve') })
}

const handleReject = () => {
  if (isProcessing.value) return
  isProcessing.value = true
  emit('reject', { decisions: buildDecisions('reject') })
}

const handleEdit = () => {
  if (isProcessing.value) return
  const parsed = parseEditedArgs()
  if (parsed === null) return
  isProcessing.value = true
  emit('edit', { decisions: buildDecisions('edit', { args: parsed }) })
}

// 确认执行：若正在编辑参数则走 edit，否则走 approve
const handlePrimary = () => {
  if (editingIndex.value !== null) {
    handleEdit()
  } else {
    handleApprove()
  }
}

defineExpose({ handlePrimary })

watch(
  () => props.visible,
  (val) => {
    if (!val) {
      isProcessing.value = false
      editingIndex.value = null
      editText.value = ''
      editError.value = ''
    }
  }
)
</script>

<style scoped>
.approval-modal {
  background: var(--gray-0);
  border-radius: 12px;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.12);
  margin: 0 auto 8px;
  max-width: 800px;
  min-width: 360px;
  width: fit-content;
  border: 1px solid var(--gray-200);
}

.approval-content {
  padding: 16px 20px;
}

.approval-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.approval-header h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--gray-800);
}

.action-count {
  font-size: 12px;
  color: var(--gray-600);
}

.action-block {
  background: var(--gray-50);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.action-block:last-of-type {
  margin-bottom: 0;
}

.action-meta {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 4px;
}

.action-label {
  color: var(--gray-600);
  flex-shrink: 0;
}

.action-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--main-700);
  font-weight: 600;
  word-break: break-all;
}

.action-args {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--gray-800);
  white-space: pre-wrap;
  word-break: break-all;
}

.action-desc {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--gray-600);
  white-space: pre-wrap;
}

.edit-area {
  margin-top: 8px;
}

.edit-textarea {
  width: 100%;
  min-height: 96px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  resize: vertical;
  outline: none;
  box-sizing: border-box;
}

.edit-textarea:focus {
  border-color: var(--main-color);
}

.edit-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: #d93025;
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--gray-600);
}

.approval-actions {
  display: flex;
  gap: 10px;
  padding: 12px 20px 16px;
}

.btn {
  flex: 1;
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-reject {
  background: var(--gray-100);
  color: var(--gray-700);
}

.btn-reject:hover:not(:disabled) {
  background: var(--gray-200);
}

.btn-edit {
  background: var(--gray-50);
  color: var(--gray-700);
  border: 1px solid var(--gray-200);
}

.btn-edit:hover:not(:disabled) {
  background: var(--gray-100);
}

.btn-approve {
  background: var(--main-color);
  color: var(--gray-0);
}

.btn-approve:hover:not(:disabled) {
  background: var(--main-700);
}

.approval-processing {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px;
  color: var(--gray-600);
  font-size: 13px;
  background: var(--gray-50);
  border-top: 1px solid var(--gray-100);
  border-radius: 0 0 12px 12px;
}

.processing-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--gray-200);
  border-top-color: var(--main-color);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.25s ease;
}

.slide-up-enter-from {
  opacity: 0;
  transform: translateY(20px);
}

.slide-up-leave-to {
  opacity: 0;
  transform: translateY(20px);
}

@media (max-width: 520px) {
  .approval-modal {
    width: calc(100vw - 12px);
    min-width: 0;
  }

  .approval-content {
    padding: 12px 16px;
  }

  .btn {
    padding: 8px 16px;
    font-size: 13px;
  }
}
</style>
