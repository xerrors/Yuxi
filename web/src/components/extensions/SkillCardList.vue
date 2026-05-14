<template>
  <div class="skill-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索技能..." v-model:search="searchQuery">
      <template #actions>
        <a-button
          @click="handleOpenRemoteInstall"
          :disabled="loading || importing"
          class="lucide-icon-btn"
        >
          <Computer :size="14" />
          <span>远程安装</span>
        </a-button>
        <a-upload
          accept=".zip,.md"
          :show-upload-list="false"
          :custom-request="handleImportUpload"
          :before-upload="beforeSkillUpload"
          :disabled="loading || importing"
        >
          <a-button type="primary" :loading="importing" class="lucide-icon-btn">
            <Upload :size="14" />
            <span>上传 Skill</span>
          </a-button>
        </a-upload>
        <a-tooltip title="刷新 Skills">
          <a-button class="lucide-icon-btn" :disabled="loading" @click="fetchSkills">
            <RefreshCw :size="14" />
          </a-button>
        </a-tooltip>
      </template>
    </PageShoulder>

    <div
      v-if="filteredInstalledSkills.length === 0 && filteredUninstalledBuiltinSkills.length === 0"
      class="extension-card-grid-empty-state"
    >
      <a-empty :image="false" description="无匹配技能" />
    </div>

    <template v-else>
      <div v-if="filteredInstalledSkills.length" class="extension-section-header">
        已添加 Skills
      </div>
      <ExtensionCardGrid>
        <InfoCard
          v-for="skill in filteredInstalledSkills"
          :key="skill.slug"
          :title="skill.name"
          :description="skill.description || '暂无描述'"
          :default-icon="BookMarkedIcon"
          :tags="skillTags(skill)"
          :status="{ label: '已安装', level: 'success' }"
          @click="navigateToDetail(skill)"
        >
        </InfoCard>
      </ExtensionCardGrid>

      <div v-if="filteredUninstalledBuiltinSkills.length" class="extension-section-header">
        可添加 Skills
      </div>
      <ExtensionCardGrid v-if="filteredUninstalledBuiltinSkills.length">
        <InfoCard
          v-for="skill in filteredUninstalledBuiltinSkills"
          :key="skill.slug"
          :title="skill.name"
          :description="skill.description || '暂无描述'"
          :default-icon="BookMarkedIcon"
          :tags="[{ name: '内置' }]"
          action-label="安装"
          @action-click="handleInstallBuiltin(skill)"
        >
        </InfoCard>
      </ExtensionCardGrid>
    </template>

    <a-modal
      v-model:open="remoteInstallModalVisible"
      title="远程安装 Skill"
      :footer="null"
      width="560px"
      :closable="!installingRemoteSkill"
      :mask-closable="!installingRemoteSkill"
      :keyboard="!installingRemoteSkill"
    >
      <div class="remote-install-panel modal-mode">
        <div class="panel-header-text">
          <span class="title">基于 skills.sh 的能力拉取并导入到当前系统</span>
          <span class="desc">
            支持 `owner/repo` 或完整 GitHub URL，可前往
            <a href="https://skills.sh/" target="_blank" rel="noopener noreferrer">skills.sh</a>
            查询可用 skills
          </span>
        </div>
        <a-form layout="vertical" class="remote-install-form">
          <a-form-item label="来源仓库">
            <a-input
              v-model:value="remoteInstallForm.source"
              placeholder="anthropics/skills 或 GitHub URL"
              :disabled="installingRemoteSkill"
            />
          </a-form-item>
          <a-form-item label="Skill 名称">
            <a-select
              v-model:value="remoteInstallForm.skills"
              mode="tags"
              :options="filteredRemoteSkillOptions"
              placeholder="frontend-design"
              allow-clear
              show-search
              :disabled="installingRemoteSkill"
              :filter-option="filterRemoteSkillOption"
              :max-tag-count="6"
            />
          </a-form-item>
          <div class="remote-install-actions">
            <a-button
              :loading="listingRemoteSkills"
              :disabled="installingRemoteSkill"
              @click="handleListRemoteSkills"
            >
              查看可安装 Skills
            </a-button>
            <a-button
              type="primary"
              :loading="installingRemoteSkill"
              :disabled="listingRemoteSkills"
              @click="handleInstallRemoteSkill"
            >
              安装
            </a-button>
            <span v-if="remoteInstallStatusText" class="remote-install-status">{{
              remoteInstallStatusText
            }}</span>
          </div>
          <div v-if="remoteSkillOptions.length" class="remote-skill-summary">
            共发现 {{ remoteSkillOptions.length }} 个 skills，可按输入内容筛选候选项。
          </div>
        </a-form>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { RefreshCw, Upload, Computer, BookMarked } from 'lucide-vue-next'
import { skillApi } from '@/apis/skill_api'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'

const BookMarkedIcon = BookMarked

const router = useRouter()

const loading = ref(false)
const importing = ref(false)
const listingRemoteSkills = ref(false)
const installingRemoteSkill = ref(false)
const searchQuery = ref('')

const skills = ref([])
const builtinSkills = ref([])

const remoteInstallModalVisible = ref(false)
const remoteInstallForm = reactive({
  source: 'https://github.com/anthropics/skills',
  skills: []
})
const remoteSkillOptions = ref([])
const remoteInstallProgress = reactive({
  visible: false,
  total: 0,
  completed: 0,
  success: 0,
  failed: 0,
  currentSkill: ''
})
const remoteInstallResults = reactive({ success: [], failed: [] })

const matchesSearch = (skill) => {
  if (!searchQuery.value) return true
  const q = searchQuery.value.toLowerCase()
  return skill.name.toLowerCase().includes(q) || skill.slug.toLowerCase().includes(q)
}

const installedSkillCards = computed(() => {
  const builtinInstalledMap = new Map(
    (builtinSkills.value || [])
      .filter((skill) => skill.status !== 'not_installed')
      .map((skill) => [
        skill.slug,
        {
          ...skill,
          sourceType: 'builtin',
          sourceLabel: '内置',
          status:
            skill.status === 'update_available'
              ? { label: '更新可用', level: 'warning' }
              : { label: '已安装', level: 'success' }
        }
      ])
  )
  const importedInstalled = (skills.value || [])
    .filter((skill) => !builtinInstalledMap.has(skill.slug))
    .map((skill) => ({
      ...skill,
      sourceType: 'imported',
      sourceLabel: '导入',
      status: { label: '已上传', level: 'success' }
    }))
  return [...builtinInstalledMap.values(), ...importedInstalled]
})

const filteredInstalledSkills = computed(() => installedSkillCards.value.filter(matchesSearch))

const filteredUninstalledBuiltinSkills = computed(() => {
  return (builtinSkills.value || []).filter(
    (skill) => skill.status === 'not_installed' && matchesSearch(skill)
  )
})

const filteredRemoteSkillOptions = computed(() =>
  remoteSkillOptions.value.map((item) => ({
    value: item.name,
    label: item.description ? `${item.name} - ${item.description}` : item.name
  }))
)

const remoteInstallStatusText = computed(() => {
  if (!remoteInstallProgress.visible || !remoteInstallProgress.total) return ''
  const progressText = `[${remoteInstallProgress.completed}/${remoteInstallProgress.total}]`
  const currentSkill = remoteInstallProgress.currentSkill || ''
  const failedText =
    remoteInstallProgress.failed > 0 ? `, ${remoteInstallProgress.failed} failed` : ''
  return `${progressText} ${currentSkill}${failedText}`.trim()
})

const filterRemoteSkillOption = (input, option) => {
  const keyword = input.trim().toLowerCase()
  if (!keyword) return true
  const value = String(option?.value || '').toLowerCase()
  const label = String(option?.label || '').toLowerCase()
  return value.includes(keyword) || label.includes(keyword)
}

const normalizeRemoteSkillNames = (skills) => {
  const seen = new Set()
  return (skills || []).reduce((acc, skill) => {
    const normalized = String(skill || '').trim()
    if (!normalized || seen.has(normalized)) return acc
    seen.add(normalized)
    acc.push(normalized)
    return acc
  }, [])
}

const resetRemoteInstallState = () => {
  remoteInstallProgress.visible = false
  remoteInstallProgress.total = 0
  remoteInstallProgress.completed = 0
  remoteInstallProgress.success = 0
  remoteInstallProgress.failed = 0
  remoteInstallProgress.currentSkill = ''
  remoteInstallResults.success = []
  remoteInstallResults.failed = []
}

const skillTags = (skill) => {
  if (skill.sourceType === 'builtin') return [{ name: skill.sourceLabel || '内置' }]
  return [{ name: skill.sourceLabel || '外部', color: 'blue' }]
}

const navigateToDetail = (skill) => {
  router.push({ path: `/extensions/skill/${encodeURIComponent(skill.slug)}` })
}

const fetchSkills = async () => {
  loading.value = true
  try {
    const [skillResult, builtinResult] = await Promise.all([
      skillApi.listSkills(),
      skillApi.listBuiltinSkills()
    ])
    skills.value = skillResult?.data || []
    builtinSkills.value = (builtinResult?.data || []).map((item) => ({
      ...item,
      ...(item.installed_record || {}),
      is_builtin_spec: true
    }))
  } catch {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const handleInstallBuiltin = async (record) => {
  if (!record?.slug) return
  loading.value = true
  try {
    await skillApi.installBuiltinSkill(record.slug)
    await fetchSkills()
    message.success('安装成功')
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '安装失败')
  } finally {
    loading.value = false
  }
}

const beforeSkillUpload = (file) => {
  const lower = file.name.toLowerCase()
  if (!lower.endsWith('.zip') && lower !== 'skill.md') {
    message.error('仅支持上传 .zip 文件或 SKILL.md 文件')
    return false
  }
  return true
}

const handleImportUpload = async ({ file, onSuccess, onError }) => {
  importing.value = true
  try {
    const result = await skillApi.importSkillZip(file)
    message.success('导入完成')
    await fetchSkills()
    onSuccess?.(result)
  } catch (e) {
    message.error('导入失败')
    onError?.(e)
  } finally {
    importing.value = false
  }
}

const handleOpenRemoteInstall = () => {
  if (!remoteInstallModalVisible.value) {
    remoteInstallForm.skills = []
    resetRemoteInstallState()
  }
  remoteInstallModalVisible.value = true
}

const handleListRemoteSkills = async () => {
  const source = remoteInstallForm.source.trim()
  if (!source) {
    message.warning('请输入来源仓库')
    return
  }
  listingRemoteSkills.value = true
  try {
    const result = await skillApi.listRemoteSkills(source)
    remoteSkillOptions.value = result?.data || []
    remoteInstallForm.skills = normalizeRemoteSkillNames(remoteInstallForm.skills)
    if (!remoteSkillOptions.value.length) {
      message.warning('未发现可安装的 Skills')
      return
    }
    message.success(`已发现 ${remoteSkillOptions.value.length} 个 Skills`)
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '获取远程 Skills 失败')
  } finally {
    listingRemoteSkills.value = false
  }
}

const handleInstallRemoteSkill = async () => {
  const source = remoteInstallForm.source.trim()
  const skillsToInstall = normalizeRemoteSkillNames(remoteInstallForm.skills)
  if (!source || !skillsToInstall.length) {
    message.warning('请填写来源仓库和 Skill 名称')
    return
  }
  remoteInstallForm.skills = skillsToInstall
  resetRemoteInstallState()
  installingRemoteSkill.value = true
  remoteInstallProgress.visible = true
  remoteInstallProgress.total = skillsToInstall.length
  try {
    const result = await skillApi.installRemoteSkillsBatch({
      source,
      skills: skillsToInstall
    })
    const results = result?.data || []
    remoteInstallResults.success = results.filter((r) => r.success).map((r) => r.slug)
    remoteInstallResults.failed = results
      .filter((r) => !r.success)
      .map((r) => ({ skill: r.slug, error: r.error || '安装失败' }))
    remoteInstallProgress.success = remoteInstallResults.success.length
    remoteInstallProgress.failed = remoteInstallResults.failed.length
    remoteInstallProgress.completed = results.length
    remoteInstallProgress.currentSkill = ''
    await fetchSkills()
    if (remoteInstallResults.failed.length === 0) {
      remoteInstallModalVisible.value = false
      message.success(`远程 Skills 安装成功，共 ${remoteInstallResults.success.length} 个`)
      resetRemoteInstallState()
      remoteInstallForm.skills = []
      return
    }
    message.warning(
      `远程 Skills 安装完成，成功 ${remoteInstallResults.success.length} 个，失败 ${remoteInstallResults.failed.length} 个`
    )
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '远程 Skill 安装失败')
  } finally {
    remoteInstallProgress.currentSkill = ''
    installingRemoteSkill.value = false
  }
}

watch(remoteInstallModalVisible, (visible) => {
  if (!visible && !installingRemoteSkill.value) {
    remoteInstallForm.skills = []
    resetRemoteInstallState()
  }
})

onMounted(() => {
  fetchSkills()
})

defineExpose({
  fetchSkills,
  handleImportUpload,
  openRemoteInstallModal: handleOpenRemoteInstall,
  loading
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
</style>

<style lang="less" scoped>
.extension-card-grid-empty-state {
  background: linear-gradient(180deg, var(--gray-0) 0%, var(--gray-50) 100%);
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  padding: 16px;

  &.modal-mode {
    border: none;
    border-radius: 0;
    padding: 0;
    background: transparent;
  }

  .panel-header-text {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;

    .title {
      font-size: 14px;
      font-weight: 600;
      color: var(--gray-900);
    }

    .desc {
      font-size: 12px;
      color: var(--gray-500);
    }
  }

  .remote-install-form {
    :deep(.ant-form-item) {
      margin-bottom: 12px;
    }
  }

  .remote-install-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .remote-install-status {
    min-width: 0;
    flex: 1;
    font-size: 12px;
    color: var(--gray-600);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .remote-skill-summary {
    margin-top: 12px;
    font-size: 12px;
    color: var(--gray-500);
  }
}
</style>
