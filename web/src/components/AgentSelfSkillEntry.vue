<template>
  <div class="agent-self-skill-entry">
    <div class="self-skill-header">
      <div class="self-skill-title">
        <span>SKILL.md</span>
        <span class="self-skill-beta">beta</span>
        <a-tooltip placement="right">
          <template #title>
            这份内容只属于当前智能体：不进入普通技能列表与聊天提及，由智能体在首轮自动加载完整说明，修改对后续新对话生效。
          </template>
          <QuestionCircleOutlined class="self-skill-help" />
        </a-tooltip>
      </div>
      <a-button
        v-if="exists"
        class="self-skill-manage-btn"
        size="small"
        type="link"
        :disabled="!canManage"
        @click="openSkillManagement"
      >
        <span class="self-skill-manage-label">
          打开技能管理
          <ExternalLink :size="12" />
        </span>
      </a-button>
    </div>

    <div class="self-skill-body">
      <a-skeleton v-if="loading || previewLoading" class="self-skill-skeleton" active :paragraph="{ rows: 4 }" />
      <template v-else-if="loaded">
        <template v-if="exists">
          <p v-if="previewError" class="self-skill-hint">{{ previewError }}</p>
          <MarkdownPreview v-else-if="previewContent" class="self-skill-content" :content="previewContent" />
          <p v-else class="self-skill-hint">SKILL.md 尚无内容。</p>
        </template>
        <template v-else>
          <p class="self-skill-hint">尚未创建，创建后可在技能管理页编辑 SKILL.md 与目录结构。</p>
          <div class="self-skill-actions">
            <a-button type="primary" :loading="creating" :disabled="!canManage" @click="createSelfSkill">
              <span class="self-skill-action-label">
                <Plus :size="14" />
                创建
              </span>
            </a-button>
            <a-button :loading="uploading" :disabled="!canManage" @click="triggerUpload">
              <span class="self-skill-action-label">
                <Upload :size="14" />
                上传
              </span>
            </a-button>
            <input
              ref="fileInputRef"
              class="self-skill-file-input"
              type="file"
              accept=".zip,.md"
              aria-label="上传专属技能"
              @change="handleFileSelected"
            />
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { ExternalLink, Plus, Upload } from '@lucide/vue'
import { agentApi } from '@/apis/agent_api'
import { skillApi } from '@/apis/skill_api'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  agentId: {
    type: String,
    default: ''
  },
  canManage: {
    type: Boolean,
    default: false
  }
})

const router = useRouter()
const loading = ref(false)
const creating = ref(false)
const loaded = ref(false)
const exists = ref(false)
const slug = ref('')
const previewContent = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const uploading = ref(false)
const fileInputRef = ref(null)

/** 读取 SKILL.md 内容用于只读预览；文件缺失按空态提示，不视为失败。 */
async function loadPreview() {
  if (!exists.value || !slug.value) return
  previewLoading.value = true
  previewError.value = ''
  previewContent.value = ''
  try {
    const result = await skillApi.getSkillFile(slug.value, 'SKILL.md')
    previewContent.value = String(result?.data?.content || '')
  } catch (error) {
    const reason = String(error?.message || '')
    previewError.value = reason.includes('文件不存在') ? 'SKILL.md 尚未创建，可在技能管理页新建。' : `读取 SKILL.md 失败：${reason || '未知错误'}`
  } finally {
    previewLoading.value = false
  }
}

async function load() {
  if (!props.agentId) return
  loading.value = true
  previewContent.value = ''
  previewError.value = ''
  try {
    const result = await agentApi.getAgentSelfSkill(props.agentId)
    exists.value = Boolean(result?.exists)
    slug.value = result?.slug || ''
    loaded.value = true
    await loadPreview()
  } catch (error) {
    message.error(`读取 Agent 专属技能失败：${error?.message || '未知错误'}`)
  } finally {
    loading.value = false
  }
}

async function createSelfSkill() {
  if (!props.agentId) return
  creating.value = true
  try {
    const result = await agentApi.createAgentSelfSkill(props.agentId)
    slug.value = result?.slug || slug.value
    exists.value = true
    await loadPreview()
  } catch (error) {
    message.error(`创建失败：${error?.message || '未知错误'}`)
  } finally {
    creating.value = false
  }
}

function openSkillManagement() {
  if (!props.agentId || !slug.value) return
  router.push(`/extensions/skill/${encodeURIComponent(slug.value)}`)
}

function triggerUpload() {
  if (!props.agentId) return
  fileInputRef.value?.click()
}

async function handleFileSelected(event) {
  const file = event.target.files?.[0]
  // 立即清空，保证同一文件可以再次触发 change
  event.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    await agentApi.uploadAgentSelfSkill(props.agentId, file)
    // 上传后 slug 不变（按 Agent 派生），重新读取绑定与预览
    await load()
  } catch (error) {
    message.error(`上传失败：${error?.message || '未知错误'}`)
  } finally {
    uploading.value = false
  }
}

watch(
  () => props.agentId,
  () => load(),
  { immediate: true }
)
</script>

<style lang="less" scoped>
.agent-self-skill-entry {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.self-skill-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: none;
}

.self-skill-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

.self-skill-beta {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--gray-900);
  color: var(--gray-0);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
}

.self-skill-help {
  color: var(--gray-500);
  cursor: help;
  font-size: 14px;
}

.self-skill-manage-btn {
  font-size: 12px;
  color: var(--gray-800);

  &:not(:disabled):hover {
    color: var(--gray-900);
  }
}

.self-skill-manage-label {
  display: inline-flex;
  gap: 4px;
}

.self-skill-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.self-skill-skeleton {
  align-self: stretch;
  width: 100%;
}

.self-skill-content {
  flex: 1;
  min-height: 0;
  align-self: stretch;
  overflow-y: auto;
}

.self-skill-hint {
  margin: 0;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 1.6;
}

.self-skill-actions {
  display: flex;
  flex: none;
  margin-top: 10px;
  gap: 8px;
}

.self-skill-action-label {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.self-skill-file-input {
  display: none;
}
</style>
