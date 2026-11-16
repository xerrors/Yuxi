<template>
  <div class="skill-cards-page extension-page-root">
    <PageShoulder search-placeholder="搜索技能..." v-model:search="searchQuery">
      <template #actions>
        <template v-if="!isBatchDeleteMode">
          <a-button
            @click="isBatchDeleteMode = true"
            :disabled="loading || importing || filteredDeletableSkills.length === 0"
            class="lucide-icon-btn"
          >
            <span>批量管理</span>
          </a-button>
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
          <a-button
            class="lucide-icon-btn"
            aria-label="刷新 Skills"
            :disabled="loading"
            @click="fetchSkills({ refreshPersonal: true })"
          >
            <RefreshCw :size="14" />
          </a-button>
        </template>
        <template v-else>
          <a-button size="small" type="link" @click="handleBatchSelectAll">全选</a-button>
          <a-button size="small" type="link" @click="handleBatchSelectInvert">反选</a-button>
          <a-button size="small" type="link" @click="handleBatchSelectNone">清空</a-button>
          <a-button
            type="primary"
            danger
            :disabled="selectedCardSlugs.length === 0"
            :loading="loading"
            @click="handleBatchDelete"
          >
            批量删除 ({{ selectedCardSlugs.length }})
          </a-button>
          <a-button :disabled="loading" @click="exitBatchDeleteMode">退出管理</a-button>
        </template>
      </template>
    </PageShoulder>

    <div
      v-if="visibleSkillGroups.length === 0"
      class="extension-card-grid-empty-state skill-empty-state"
    >
      <div class="skill-empty-card">
        <div class="skill-empty-icon">
          <WandSparkles :size="22" />
        </div>
        <div class="skill-empty-title">
          {{ searchQuery ? '没有匹配的 Skill' : '还没有添加 Skill' }}
        </div>
        <div class="skill-empty-desc">
          {{
            searchQuery
              ? '换个关键词试试，或清空搜索条件。'
              : '可以从远程仓库安装，或上传本地 Skill 文件。'
          }}
        </div>
      </div>
    </div>

    <template v-else>
      <template v-for="group in visibleSkillGroups" :key="group.key">
        <div class="extension-section-header">{{ group.title }}</div>
        <ExtensionCardGrid :min-width="280">
          <div
            v-for="skill in group.skills"
            :key="`${group.key}:${skill.slug || skill.id}`"
            class="card-wrapper"
            :class="{
              selected: !skill.isSuite && selectedCardSlugs.includes(skill.slug),
              'batch-mode': isBatchDeleteMode && !skill.isSuite
            }"
          >
            <SkillSuiteCard
              v-if="skill.isSuite"
              :suite="skill"
              :installed-slugs="[...installedPersonalSkillKeys]"
              @open="openRecommendedSuite"
            />
            <template v-else>
              <a-checkbox
                v-if="
                  isBatchDeleteMode &&
                  canManageSkill(skill) &&
                  skill.sourceType !== 'builtin' &&
                  skill.sourceScope !== 'personal'
                "
                :checked="selectedCardSlugs.includes(skill.slug)"
                @change="handleToggleCardSelect(skill.slug)"
                class="card-select-checkbox"
              />
              <InfoCard
                variant="default"
                :title="formatExtensionCardTitle(skill.name)"
                :subtitle="skill.slug"
                :description="skill.description || '暂无描述'"
                :tags="skillCardTags(skill)"
                :default-icon="getSkillIcon(skill.slug)"
                @click="handleCardClick(skill)"
                :class="{ 'card-clickable-select': isBatchDeleteMode }"
              >
                <template #actions>
                  <button
                    v-if="skill.sourceScope !== 'personal'"
                    type="button"
                    class="skill-enabled-action"
                    :class="{ enabled: skill.enabled !== false }"
                    :disabled="!canManageSkill(skill) || isSkillToggling(skill.slug)"
                    :aria-label="skill.enabled === false ? '启用 Skill' : '禁用 Skill'"
                    @click.stop="handleToggleSkillEnabled(skill)"
                  >
                    <Plus v-if="skill.enabled === false" :size="15" class="action-icon" />
                    <template v-else>
                      <Check :size="15" class="action-icon action-icon-check" />
                      <Minus :size="15" class="action-icon action-icon-minus" />
                    </template>
                  </button>
                </template>
              </InfoCard>
            </template>
          </div>
        </ExtensionCardGrid>
      </template>
    </template>

    <a-modal
      v-model:open="skillPreviewVisible"
      class="skill-preview-modal"
      :footer="null"
      width="680px"
      :closable="false"
      :destroy-on-close="true"
      @cancel="closeSkillPreview"
    >
      <div v-if="previewSkill" class="skill-preview-panel">
        <div class="skill-preview-header">
          <div class="skill-preview-title-area">
            <div class="skill-preview-icon">
              <component :is="getSkillIcon(previewSkill.slug)" :size="18" />
            </div>
            <div class="skill-preview-title-text">
              <div class="skill-preview-title">
                {{ formatExtensionCardTitle(previewSkill.name) }}
              </div>
              <div class="skill-preview-meta">
                <span
                  >{{
                    sourceTypeLabel(previewSkill.sourceType || previewSkill.source_type)
                  }}
                  Skill</span
                >
                <span v-if="previewSkill.enabled === false" class="skill-preview-disabled-tag">
                  已禁用
                </span>
              </div>
            </div>
          </div>
          <div class="skill-preview-actions">
            <a-switch
              v-if="previewSkill.sourceScope !== 'personal'"
              :checked="previewSkill.enabled !== false"
              :disabled="!canManageSkill(previewSkill) || isSkillToggling(previewSkill.slug)"
              :loading="isSkillToggling(previewSkill.slug)"
              size="small"
              @change="handlePreviewToggle"
            />
          </div>
        </div>

        <div class="skill-preview-body">
          <div v-if="skillPreviewLoading" class="skill-preview-loading">
            <a-spin />
          </div>
          <MarkdownPreview
            v-else-if="skillPreviewMarkdown"
            :content="skillPreviewMarkdown"
            :compact="true"
          />
          <a-empty v-else :description="skillPreviewError || '未读取到 SKILL.md'" />
        </div>

        <div class="skill-preview-footer">
          <div class="skill-preview-footer-left">
            <a-button
              v-if="canDeletePreviewSkill"
              danger
              :loading="deletingPreviewSkill"
              @click="confirmDeletePreviewSkill"
            >
              卸载
            </a-button>
          </div>
          <div class="skill-preview-footer-right">
            <a-button @click="closeSkillPreview">关闭</a-button>
            <a-button
              v-if="previewSkill.sourceScope !== 'personal'"
              type="primary"
              class="lucide-icon-btn"
              @click="goToPreviewSkillManagement"
            >
              <span>去管理</span>
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <SkillInstallFlowModal
      :open="installFlowOpen"
      :flow="installFlow"
      @close="closeInstallFlow"
      @completed="handleInstallFlowCompleted"
    >
      <template #selection>
        <div class="remote-install-panel">
          <a-tabs v-model:activeKey="activeTab" class="install-tabs">
            <!-- Tab 1: 按仓库拉取 -->
            <a-tab-pane key="repo" tab="按仓库拉取">
              <div class="tab-content-wrapper">
                <a-form layout="vertical" class="remote-install-form">
                  <div class="repo-input-row">
                    <div class="repo-input-field">
                      <a-input
                        v-model:value="remoteInstallForm.source"
                        placeholder="来源仓库或合集地址，如 https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills"
                      >
                        <template #suffix>
                          <a-dropdown
                            :trigger="['click']"
                            placement="bottomRight"
                            overlay-class-name="history-dropdown-menu"
                          >
                            <div class="history-trigger-wrapper">
                              <a-tooltip title="历史仓库">
                                <History
                                  :size="14"
                                  class="history-icon-trigger"
                                  :class="{ 'has-history': repoHistory.length > 0 }"
                                />
                              </a-tooltip>
                            </div>
                            <template #overlay>
                              <a-menu @click="handleSelectHistory">
                                <a-menu-item v-if="repoHistory.length === 0" disabled>
                                  <span class="history-empty-text">暂无使用历史</span>
                                </a-menu-item>
                                <template v-else>
                                  <a-menu-item v-for="item in repoHistory" :key="item">
                                    <div class="history-item-menu-row">
                                      <span class="history-item-text" :title="item">{{
                                        item
                                      }}</span>
                                      <span
                                        class="history-item-del-btn"
                                        @click.stop="deleteHistoryItem(item)"
                                      >
                                        <Trash2 :size="12" />
                                      </span>
                                    </div>
                                  </a-menu-item>
                                  <a-menu-divider />
                                  <a-menu-item
                                    key="clear-all-history"
                                    class="clear-history-menu-item"
                                  >
                                    <div class="clear-history-btn-content">
                                      <Trash2 :size="12" class="clear-icon" />
                                      <span>清空历史记录</span>
                                    </div>
                                  </a-menu-item>
                                </template>
                              </a-menu>
                            </template>
                          </a-dropdown>
                        </template>
                      </a-input>
                    </div>
                    <a-button
                      type="primary"
                      :loading="listingRemoteSkills"
                      @click="handleListRemoteSkills"
                    >
                      拉取技能
                    </a-button>
                  </div>
                  <div class="repo-hint-text">
                    支持 `owner/repo` 或 GitHub URL。可前往
                    <a href="https://skills.sh/" target="_blank" rel="noopener noreferrer"
                      >skills.sh</a
                    >
                    查询开源 skills。 也支持 ModelScope 单个 Skill
                    地址，每次仅限安装一个：`https://modelscope.cn/skills/&lt;skill-id&gt;`。 Skill
                    ID 可在
                    <a href="https://modelscope.cn/skills" target="_blank" rel="noopener noreferrer"
                      >ModelScope Skill 市场</a
                    >
                    进入详情后从地址栏获取。
                  </div>

                  <!-- 仓库技能多选列表 -->
                  <div v-if="remoteSkillOptions.length" class="skills-list-section">
                    <template v-if="hasSingleRepoSkill">
                      <div class="single-remote-skill-card">
                        <div class="single-remote-skill-name">{{ singleRepoSkill.name }}</div>
                        <div class="single-remote-skill-meta">
                          {{ singleRepoSkill.description || '暂无描述' }}
                        </div>
                      </div>
                    </template>
                    <template v-else>
                      <div class="list-operations-bar">
                        <div class="op-buttons">
                          <a-button size="small" type="link" @click="handleRepoSelectAll"
                            >全选</a-button
                          >
                          <a-button size="small" type="link" @click="handleRepoSelectInvert"
                            >反选</a-button
                          >
                          <a-button size="small" type="link" @click="handleRepoSelectNone"
                            >清空</a-button
                          >
                        </div>
                        <a-input
                          v-model:value="repoFilterKeyword"
                          placeholder="本地过滤检索..."
                          size="small"
                          style="width: 180px"
                          allow-clear
                        />
                      </div>
                      <div class="skills-list-viewport">
                        <div class="remote-skills-list-container">
                          <div
                            v-for="item in filteredRepoSkills"
                            :key="item.name"
                            class="remote-skill-row"
                            :class="{ selected: selectedRepoSkills.includes(item.name) }"
                            role="checkbox"
                            tabindex="0"
                            :aria-checked="selectedRepoSkills.includes(item.name)"
                            @click="toggleRepoSkillFromRow(item.name)"
                            @keydown.enter.prevent="toggleRepoSkillFromRow(item.name)"
                            @keydown.space.prevent="toggleRepoSkillFromRow(item.name)"
                          >
                            <a-checkbox
                              class="remote-row-checkbox"
                              :checked="selectedRepoSkills.includes(item.name)"
                              :tabindex="-1"
                              aria-hidden="true"
                            />
                            <div class="remote-skill-row-content">
                              <span class="skill-item-name">{{ item.name }}</span>
                              <span class="skill-item-desc">{{
                                item.description || '暂无描述'
                              }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </a-form>
              </div>
            </a-tab-pane>

            <!-- Tab 2: 全局搜索发现 -->
            <a-tab-pane key="search" tab="全局搜索发现">
              <div class="tab-content-wrapper">
                <a-form layout="vertical" class="remote-install-form">
                  <div class="repo-input-row">
                    <div class="repo-input-field">
                      <a-input
                        v-model:value="searchKeyword"
                        placeholder="输入 web、python 等关键字进行全局查找"
                        @pressEnter="handleSearchRemoteSkills"
                      />
                    </div>
                    <a-button
                      type="primary"
                      :loading="searchingRemoteSkills"
                      @click="handleSearchRemoteSkills"
                    >
                      查找技能
                    </a-button>
                  </div>
                  <div class="repo-hint-text">
                    直接输入关键字检索 skills.sh 上的开源 Skills 并批量拉取安装。
                  </div>

                  <!-- 搜索结果列表 -->
                  <div v-if="searchedSkills.length" class="skills-list-section">
                    <template v-if="hasSingleSearchedSkill">
                      <div class="single-remote-skill-card">
                        <div class="single-remote-skill-header">
                          <div class="single-remote-skill-name">
                            {{ singleSearchedSkill.name }}
                          </div>
                          <a-tag v-if="singleSearchedSkill.installs" class="skill-item-installs">
                            {{ singleSearchedSkill.installs }}
                          </a-tag>
                        </div>
                        <div class="single-remote-skill-meta">
                          {{ singleSearchedSkill.source }}
                        </div>
                      </div>
                    </template>
                    <template v-else>
                      <div class="list-operations-bar">
                        <div class="op-buttons">
                          <a-button size="small" type="link" @click="handleSearchSelectAll"
                            >全选</a-button
                          >
                          <a-button size="small" type="link" @click="handleSearchSelectInvert"
                            >反选</a-button
                          >
                          <a-button size="small" type="link" @click="handleSearchSelectNone"
                            >清空</a-button
                          >
                        </div>
                      </div>
                      <div class="skills-list-viewport">
                        <div class="remote-skills-list-container">
                          <div
                            v-for="item in searchedSkills"
                            :key="`${item.source}:${item.name}`"
                            class="remote-skill-row"
                            :class="{ selected: isSearchSkillSelected(item) }"
                            role="checkbox"
                            tabindex="0"
                            :aria-checked="isSearchSkillSelected(item)"
                            @click="toggleSearchSkillFromRow(item)"
                            @keydown.enter.prevent="toggleSearchSkillFromRow(item)"
                            @keydown.space.prevent="toggleSearchSkillFromRow(item)"
                          >
                            <a-checkbox
                              class="remote-row-checkbox"
                              :checked="isSearchSkillSelected(item)"
                              :tabindex="-1"
                              aria-hidden="true"
                            />
                            <div class="remote-skill-row-content">
                              <span class="skill-item-name">{{ item.name }}</span>
                              <span class="skill-item-desc">{{ item.source }}</span>
                            </div>
                            <span v-if="item.installs" class="skill-install-count">
                              {{ item.installs }}
                            </span>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </a-form>
              </div>
            </a-tab-pane>
          </a-tabs>
        </div>
      </template>

      <template #selection-footer>
        <span class="modal-footer-summary">{{ remoteSelectionSummary }}</span>
        <div class="modal-footer-buttons">
          <a-button @click="closeInstallFlow">取消</a-button>
          <a-button
            type="primary"
            :disabled="
              activeTab === 'repo'
                ? selectedRepoSkills.length === 0
                : selectedSearchSkills.length === 0
            "
            @click="startInstallRemoteSkills"
          >
            解析并确认
          </a-button>
        </div>
      </template>
    </SkillInstallFlowModal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  RefreshCw,
  Upload,
  Computer,
  WandSparkles,
  History,
  Trash2,
  Check,
  Plus,
  Minus
} from '@lucide/vue'
import { skillApi } from '@/apis/skill_api'
import ExtensionCardGrid from './ExtensionCardGrid.vue'
import SkillInstallFlowModal from './SkillInstallFlowModal.vue'
import SkillSuiteCard from './SkillSuiteCard.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { formatExtensionCardTitle } from '@/utils/extensionDisplayName'
import { getShareConfigLabel } from '@/utils/shareConfig'
import { getSkillIcon } from '@/utils/skill_icon_utils'

const RECOMMENDED_SUITES = [
  {
    id: 'minimax-office-skills',
    name: 'MiniMax 办公文档套件',
    provider: 'MiniMax-AI',
    description:
      'MiniMax 开源的办公文档 Skills 合集，覆盖 DOCX、PDF、XLSX 与 PPTX 演示文稿的创建与格式化。',
    source: 'https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills',
    skills: [
      {
        slug: 'pptx-generator',
        name: 'pptx-generator',
        description:
          '生成、编辑和阅读 PowerPoint 演示文稿。使用 PptxGenJS 从头开始创建，通过 XML 工作流编辑现有的 PPTX，或使用 markitdown 提取文本。'
      },
      {
        slug: 'minimax-docx',
        name: 'minimax-docx',
        description:
          '使用 OpenXML SDK（.NET）进行专业的 DOCX 文档创建、编辑和格式化，支持模板应用与 XSD 验证门控检查。'
      },
      {
        slug: 'minimax-xlsx',
        name: 'minimax-xlsx',
        description:
          '创建、读取、分析、编辑或验证 Excel/电子表格文件，支持公式重算校验与专业财务格式标准。'
      },
      {
        slug: 'minimax-pdf',
        name: 'minimax-pdf',
        description: '高视觉质量与设计感的 PDF 生成、表单字段填写、样式转换与专业打印级文档排版。'
      }
    ]
  },
  {
    id: 'skill-builder-suite',
    name: 'Skill 能力与进化套件',
    provider: 'Community',
    description: '用于 Agent 技能发现、创建、评测调优与自主进化的核心工具合集。',
    skills: [
      {
        slug: 'skill-creator',
        name: 'skill-creator',
        source: 'https://modelscope.cn/skills/@anthropics/skill-creator',
        description: '创建新技能、修改与优化现有技能，并通过方差基准分析评测技能表现与调优描述。'
      },
      {
        slug: 'find-skills',
        name: 'find-skills',
        source: 'https://modelscope.cn/skills/@vercel-labs/find-skills',
        description: '协助智能体根据用户需求检索并发现可安装的开源 Agent Skills，动态扩展自身能力。'
      },
      {
        slug: 'self-improving-agent',
        name: 'self-improving-agent',
        source: 'https://github.com/zhaono1/agent-playbook',
        description: '通用自我进化技能，基于多重记忆架构从经验与错误中持续学习并自我迭代。'
      }
    ]
  }
]

const router = useRouter()

const loading = ref(false)
const importing = ref(false)
const listingRemoteSkills = ref(false)
const searchQuery = ref('')

const isBatchDeleteMode = ref(false)
const selectedCardSlugs = ref([])
const togglingSkillSlugs = ref([])

const skills = ref([])
const skillPreviewVisible = ref(false)
const previewSkill = ref(null)
const skillPreviewMarkdown = ref('')
const skillPreviewLoading = ref(false)
const skillPreviewError = ref('')
const deletingPreviewSkill = ref(false)
let previewRequestSeq = 0
const installFlowOpen = ref(false)
const installFlow = ref(null)

const activeTab = ref('repo') // 'repo' 或 'search'

const remoteInstallForm = reactive({
  source: 'https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills',
  skills: []
})
const remoteSkillOptions = ref([])
const repoFilterKeyword = ref('')
const selectedRepoSkills = ref([])
const hasSingleRepoSkill = computed(() => remoteSkillOptions.value.length === 1)
const singleRepoSkill = computed(() => remoteSkillOptions.value[0] || null)

const searchKeyword = ref('')
const searchingRemoteSkills = ref(false)
const searchedSkills = ref([])
const selectedSearchSkills = ref([])
const hasSingleSearchedSkill = computed(() => searchedSkills.value.length === 1)
const singleSearchedSkill = computed(() => searchedSkills.value[0] || null)
const remoteSelectionSummary = computed(() => {
  if (activeTab.value === 'repo') {
    if (!remoteSkillOptions.value.length) return '请先拉取仓库中的 Skill'
    return `已选 ${selectedRepoSkills.value.length} / 共发现 ${remoteSkillOptions.value.length} 个 Skill`
  }

  if (!searchedSkills.value.length) return '请输入关键词查找 Skill'
  return `已选 ${selectedSearchSkills.value.length} / 共找到 ${searchedSkills.value.length} 个 Skill`
})

const repoHistory = ref([])

const matchesSearch = (skill) => {
  if (!searchQuery.value) return true
  const q = searchQuery.value.toLowerCase()
  const text = [
    skill.name,
    skill.slug,
    skill.description,
    ...(skill.skills || []).flatMap((item) => [item.name, item.slug, item.description])
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return text.includes(q)
}

const installedSkillCards = computed(() =>
  (skills.value || []).map((skill) => ({
    ...skill,
    sourceType: skill.source_type || 'upload',
    sourceScope: skill.source_scope
  }))
)

const installedPersonalSkillKeys = computed(() => {
  const keys = new Set()
  installedSkillCards.value.forEach((skill) => {
    if (skill.sourceScope !== 'personal') return
    const identifiers = [skill.slug, skill.name]
    identifiers.forEach((value) => {
      if (value) keys.add(String(value).toLowerCase())
    })
  })
  return keys
})

const recommendedSuiteCards = computed(() =>
  RECOMMENDED_SUITES.map((suite) => ({ ...suite, isSuite: true }))
)

const filteredInstalledSkills = computed(() => installedSkillCards.value.filter(matchesSearch))
const skillGroups = computed(() => [
  {
    key: 'recommended',
    title: '推荐',
    skills: isBatchDeleteMode.value ? [] : recommendedSuiteCards.value.filter(matchesSearch)
  },
  {
    key: 'personal',
    title: '个人技能',
    skills: isBatchDeleteMode.value
      ? []
      : filteredInstalledSkills.value.filter((skill) => skill.sourceScope === 'personal')
  },
  {
    key: 'builtin',
    title: '内置',
    skills: filteredInstalledSkills.value.filter((skill) => skill.sourceType === 'builtin')
  },
  {
    key: 'uploaded',
    title: '共享',
    skills: filteredInstalledSkills.value.filter(
      (skill) => skill.sourceType !== 'builtin' && skill.sourceScope !== 'personal'
    )
  }
])
const visibleSkillGroups = computed(() => skillGroups.value.filter((group) => group.skills.length))
const filteredDeletableSkills = computed(() =>
  filteredInstalledSkills.value.filter(
    (skill) =>
      canManageSkill(skill) && skill.sourceType !== 'builtin' && skill.sourceScope !== 'personal'
  )
)
const canDeletePreviewSkill = computed(
  () =>
    !!previewSkill.value &&
    canManageSkill(previewSkill.value) &&
    previewSkill.value.sourceType !== 'builtin'
)

// 仓库拉取的技能列表过滤
const filteredRepoSkills = computed(() => {
  if (!repoFilterKeyword.value.trim()) return remoteSkillOptions.value
  const kw = repoFilterKeyword.value.trim().toLowerCase()
  return remoteSkillOptions.value.filter(
    (item) =>
      item.name.toLowerCase().includes(kw) ||
      (item.description && item.description.toLowerCase().includes(kw))
  )
})

// 批量选择/反选/清空管理
const handleRepoSelectAll = () => {
  selectedRepoSkills.value = filteredRepoSkills.value.map((item) => item.name)
}
const handleRepoSelectNone = () => {
  selectedRepoSkills.value = []
}
const handleRepoSelectInvert = () => {
  const currentSelected = new Set(selectedRepoSkills.value)
  const newSelected = []
  filteredRepoSkills.value.forEach((item) => {
    if (!currentSelected.has(item.name)) {
      newSelected.push(item.name)
    }
  })
  selectedRepoSkills.value = newSelected
}

const handleSearchSelectAll = () => {
  selectedSearchSkills.value = [...searchedSkills.value]
}
const handleSearchSelectNone = () => {
  selectedSearchSkills.value = []
}
const handleSearchSelectInvert = () => {
  const newSelected = []
  searchedSkills.value.forEach((item) => {
    const isSelected = selectedSearchSkills.value.some(
      (s) => s.name === item.name && s.source === item.source
    )
    if (!isSelected) {
      newSelected.push(item)
    }
  })
  selectedSearchSkills.value = newSelected
}

const handleToggleRepoSkill = (name, checked) => {
  if (checked) {
    if (!selectedRepoSkills.value.includes(name)) {
      selectedRepoSkills.value.push(name)
    }
  } else {
    selectedRepoSkills.value = selectedRepoSkills.value.filter((n) => n !== name)
  }
}

const toggleRepoSkillFromRow = (name) => {
  handleToggleRepoSkill(name, !selectedRepoSkills.value.includes(name))
}

const isSearchSkillSelected = (item) =>
  selectedSearchSkills.value.some(
    (skill) => skill.name === item.name && skill.source === item.source
  )

const handleToggleSearchSkill = (item, checked) => {
  if (checked) {
    const isExist = selectedSearchSkills.value.some(
      (s) => s.name === item.name && s.source === item.source
    )
    if (!isExist) {
      selectedSearchSkills.value.push(item)
    }
  } else {
    selectedSearchSkills.value = selectedSearchSkills.value.filter(
      (s) => !(s.name === item.name && s.source === item.source)
    )
  }
}

const toggleSearchSkillFromRow = (item) => {
  handleToggleSearchSkill(item, !isSearchSkillSelected(item))
}

const sourceTypeLabel = (sourceType) => {
  if (sourceType === 'personal') return '个人技能'
  if (sourceType === 'builtin') return '内置'
  if (sourceType === 'remote') return '远程'
  return '上传'
}

/** 返回 Skill 共享范围的简短展示文案。 */
const getSkillShareLabel = (skill) => getShareConfigLabel(skill?.share_config)

const skillCardTags = (skill) => {
  if (skill.sourceScope === 'personal') {
    return [
      { name: '个人技能', color: 'gray' },
      ...(skill.overrides_shared ? [{ name: '覆盖共享版本', color: 'orange' }] : [])
    ]
  }
  return [
    { name: getSkillShareLabel(skill), color: 'gray' },
    ...(skill.shadowed_by_personal ? [{ name: '已被个人版本覆盖', color: 'orange' }] : [])
  ]
}

const canManageSkill = (skill) => skill?.can_manage !== false
const isSkillToggling = (slug) => togglingSkillSlugs.value.includes(slug)
const navigateToDetail = (skill) => {
  if (skill?.sourceScope === 'personal') return
  router.push({ path: `/extensions/skill/${encodeURIComponent(skill.slug)}` })
}

const closeSkillPreview = () => {
  skillPreviewVisible.value = false
}

const openSkillPreview = async (skill) => {
  if (!skill?.slug) return
  const requestSeq = ++previewRequestSeq
  previewSkill.value = skill
  skillPreviewMarkdown.value = ''
  skillPreviewError.value = ''
  skillPreviewLoading.value = true
  skillPreviewVisible.value = true
  try {
    const result =
      skill.sourceScope === 'personal'
        ? await skillApi.getPersonalSkillFile(skill.slug, 'SKILL.md')
        : await skillApi.getSkillFile(skill.slug, 'SKILL.md')
    if (requestSeq !== previewRequestSeq || previewSkill.value?.slug !== skill.slug) return
    skillPreviewMarkdown.value = result?.data?.content || ''
  } catch (error) {
    if (requestSeq !== previewRequestSeq || previewSkill.value?.slug !== skill.slug) return
    skillPreviewError.value = error?.response?.data?.detail || error.message || '读取 SKILL.md 失败'
  } finally {
    if (requestSeq === previewRequestSeq) skillPreviewLoading.value = false
  }
}

const goToPreviewSkillManagement = () => {
  if (!previewSkill.value) return
  navigateToDetail(previewSkill.value)
  closeSkillPreview()
}

const handleCardClick = (skill) => {
  if (isBatchDeleteMode.value) {
    handleToggleCardSelect(skill.slug)
  } else {
    openSkillPreview(skill)
  }
}

const handleToggleCardSelect = (slug) => {
  const target = installedSkillCards.value.find(
    (skill) => skill.slug === slug && skill.sourceScope !== 'personal'
  )
  if (!canManageSkill(target) || target?.sourceType === 'builtin') return
  const idx = selectedCardSlugs.value.indexOf(slug)
  if (idx > -1) {
    selectedCardSlugs.value.splice(idx, 1)
  } else {
    selectedCardSlugs.value.push(slug)
  }
}

const handleToggleSkillEnabled = async (skill) => {
  if (!skill || !canManageSkill(skill) || isSkillToggling(skill.slug)) return
  const enabled = skill.enabled === false
  togglingSkillSlugs.value.push(skill.slug)
  try {
    const result = await skillApi.updateSkillEnabled(skill.slug, enabled)
    const updatedSkill = result?.data
    const index = skills.value.findIndex(
      (item) => item.slug === skill.slug && item.source_scope !== 'personal'
    )
    if (updatedSkill && index > -1) {
      skills.value[index] = updatedSkill
    } else {
      await fetchSkills()
    }
    if (previewSkill.value?.slug === skill.slug) {
      previewSkill.value = updatedSkill
        ? { ...updatedSkill, sourceType: updatedSkill.source_type || 'upload' }
        : { ...previewSkill.value, enabled }
    }
    message.success(`Skill 已${enabled ? '启用' : '禁用'}`)
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '更新 Skill 启用状态失败')
  } finally {
    togglingSkillSlugs.value = togglingSkillSlugs.value.filter((slug) => slug !== skill.slug)
  }
}

const handlePreviewToggle = () => {
  if (!previewSkill.value) return
  handleToggleSkillEnabled(previewSkill.value)
}

const confirmDeletePreviewSkill = () => {
  const target = previewSkill.value
  if (!target || !canDeletePreviewSkill.value || deletingPreviewSkill.value) return

  Modal.confirm({
    title: `卸载 ${target.name || target.slug}`,
    content:
      target.sourceScope === 'personal'
        ? '卸载后会删除个人 Skill；如有同名共享版本，Agent 将恢复使用共享版本。'
        : '卸载后会删除该 Skill 的数据库记录和本地文件，操作不可恢复。',
    okText: '卸载',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      deletingPreviewSkill.value = true
      try {
        if (target.sourceScope === 'personal') {
          await skillApi.deletePersonalSkill(target.slug)
        } else {
          await skillApi.deleteSkill(target.slug)
        }
        message.success('Skill 已卸载')
        closeSkillPreview()
        previewSkill.value = null
        await fetchSkills()
      } catch (error) {
        message.error(error?.response?.data?.detail || error.message || '卸载 Skill 失败')
      } finally {
        deletingPreviewSkill.value = false
      }
    }
  })
}

const handleBatchSelectAll = () => {
  selectedCardSlugs.value = filteredDeletableSkills.value.map((skill) => skill.slug)
}

const handleBatchSelectNone = () => {
  selectedCardSlugs.value = []
}

const handleBatchSelectInvert = () => {
  const currentSet = new Set(selectedCardSlugs.value)
  selectedCardSlugs.value = filteredDeletableSkills.value
    .filter((skill) => !currentSet.has(skill.slug))
    .map((skill) => skill.slug)
}

const exitBatchDeleteMode = () => {
  isBatchDeleteMode.value = false
  selectedCardSlugs.value = []
}

const handleBatchDelete = () => {
  const deletableSlugs = selectedCardSlugs.value.filter((slug) => {
    const target = installedSkillCards.value.find(
      (skill) => skill.slug === slug && skill.sourceScope !== 'personal'
    )
    return (
      canManageSkill(target) &&
      target?.sourceType !== 'builtin' &&
      target?.sourceScope !== 'personal'
    )
  })
  if (deletableSlugs.length === 0) return

  Modal.confirm({
    title: '确定要批量删除选中的技能吗？',
    content: `您已选中了 ${deletableSlugs.length} 个技能。该操作将从数据库和物理磁盘中彻底删除这些技能包，且不可恢复！`,
    okText: '确定删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      loading.value = true
      try {
        const res = await skillApi.deleteSkillsBatch(deletableSlugs)
        const results = res?.data || []
        const successList = results.filter((r) => r.success)
        const failList = results.filter((r) => !r.success)

        if (failList.length === 0) {
          message.success(`批量删除成功，已删除 ${successList.length} 个技能`)
        } else {
          message.warning(`批量删除完成：成功 ${successList.length} 个，失败 ${failList.length} 个`)
        }

        exitBatchDeleteMode()
        await fetchSkills()
      } catch (error) {
        message.error(error?.response?.data?.detail || error.message || '批量删除失败')
      } finally {
        loading.value = false
      }
    }
  })
}

const fetchSkills = async ({ refreshPersonal = false } = {}) => {
  loading.value = true
  try {
    const skillResult = await skillApi.listSkillCards({ refreshPersonal })
    skills.value = skillResult?.data || []
  } catch {
    message.error('加载失败')
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

const openInstallFlow = (flow) => {
  installFlow.value = flow
  installFlowOpen.value = true
}

const resetRemoteSelection = () => {
  selectedRepoSkills.value = []
  selectedSearchSkills.value = []
  remoteSkillOptions.value = []
  searchedSkills.value = []
  repoFilterKeyword.value = ''
  searchKeyword.value = ''
}

const closeInstallFlow = () => {
  const wasRemoteFlow = installFlow.value?.kind === 'remote'
  installFlowOpen.value = false
  installFlow.value = null
  if (wasRemoteFlow) resetRemoteSelection()
}

const openRecommendedSuite = (suite) => {
  openInstallFlow({
    kind: 'suite',
    suite,
    installedSlugs: [...installedPersonalSkillKeys.value]
  })
}

const handleInstallFlowCompleted = async ({ success, failed }) => {
  if (failed === 0) message.success(`已添加 ${success} 个 Skill`)
  else message.warning(`安装完成：成功 ${success} 个，失败 ${failed} 个`)
  await fetchSkills()
}

const handleImportUpload = async ({ file, onSuccess, onError }) => {
  importing.value = true
  try {
    const result = await skillApi.prepareSkillUpload(file)
    openInstallFlow({
      kind: 'draft',
      title: `安装 ${file.name}`,
      description: '检查 Skill 依赖与生效范围，然后完成安装。',
      drafts: [result?.data]
    })
    onSuccess?.(result)
  } catch (e) {
    message.error(e?.response?.data?.detail || e.message || '解析 Skill 失败')
    onError?.(e)
  } finally {
    importing.value = false
  }
}

const handleOpenRemoteInstall = () => {
  resetRemoteSelection()
  openInstallFlow({
    kind: 'remote',
    title: '远程安装 Skill',
    description: '按仓库拉取或全局搜索，选择需要安装的 Skill。'
  })
}

const rememberRemoteSource = (source) => {
  let history = [...repoHistory.value]
  history = history.filter((item) => item !== source)
  history.unshift(source)
  if (history.length > 10) {
    history = history.slice(0, 10)
  }
  repoHistory.value = history
  localStorage.setItem('yuxi_remote_repo_history', JSON.stringify(history))
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
    selectedRepoSkills.value =
      remoteSkillOptions.value.length === 1 ? [remoteSkillOptions.value[0].name] : []
    if (!remoteSkillOptions.value.length) {
      message.warning('未发现可安装的 Skills')
      return
    }
    if (remoteSkillOptions.value.length === 1) {
      message.success('已发现 1 个 Skill，已自动选中')
    } else {
      message.success(`已发现 ${remoteSkillOptions.value.length} 个 Skills`)
    }

    rememberRemoteSource(source)
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '获取远程 Skills 失败')
  } finally {
    listingRemoteSkills.value = false
  }
}

const loadHistory = () => {
  try {
    const raw = localStorage.getItem('yuxi_remote_repo_history')
    if (raw) {
      repoHistory.value = JSON.parse(raw)
    }
  } catch (e) {
    console.error('Failed to load repo history', e)
  }
}

const deleteHistoryItem = (item) => {
  repoHistory.value = repoHistory.value.filter((h) => h !== item)
  localStorage.setItem('yuxi_remote_repo_history', JSON.stringify(repoHistory.value))
}

const clearAllHistory = () => {
  repoHistory.value = []
  localStorage.removeItem('yuxi_remote_repo_history')
  message.success('历史记录已清空')
}

const handleSelectHistory = ({ key }) => {
  if (key === 'clear-all-history') {
    clearAllHistory()
    return
  }
  remoteInstallForm.source = key
}

const handleSearchRemoteSkills = async () => {
  const query = searchKeyword.value.trim()
  if (!query) {
    message.warning('请输入搜索关键字')
    return
  }
  searchingRemoteSkills.value = true
  try {
    const result = await skillApi.searchRemoteSkills(query)
    searchedSkills.value = result?.data || []
    selectedSearchSkills.value = searchedSkills.value.length === 1 ? [...searchedSkills.value] : []
    if (!searchedSkills.value.length) {
      message.warning('未搜索到相关的 Skills')
    } else if (searchedSkills.value.length === 1) {
      message.success('搜索到 1 个 Skill，已自动选中')
    } else {
      message.success(`搜索到 ${searchedSkills.value.length} 个 Skills`)
    }
  } catch (error) {
    message.error(error?.response?.data?.detail || error.message || '搜索远程 Skills 失败')
  } finally {
    searchingRemoteSkills.value = false
  }
}

const startInstallRemoteSkills = () => {
  const requests = []
  if (activeTab.value === 'repo') {
    requests.push({
      source: remoteInstallForm.source.trim(),
      skills: [...selectedRepoSkills.value],
      skillDetails: remoteSkillOptions.value
        .filter((item) => selectedRepoSkills.value.includes(item.name))
        .map((item) => ({ ...item, slug: item.name }))
    })
  } else {
    const groups = new Map()
    selectedSearchSkills.value.forEach((item) => {
      if (!groups.has(item.source)) groups.set(item.source, [])
      groups.get(item.source).push(item)
    })
    groups.forEach((items, source) => {
      requests.push({
        source,
        skills: items.map((item) => item.name),
        skillDetails: items.map((item) => ({ ...item, slug: item.name }))
      })
    })
  }

  openInstallFlow({
    kind: 'remote',
    title: '远程安装 Skill',
    description: `${requests.length} 个来源 · ${requests.reduce((total, item) => total + item.skills.length, 0)} 个 Skill`,
    requests
  })
}

watch(activeTab, () => {
  selectedRepoSkills.value = []
  selectedSearchSkills.value = []
})

onMounted(() => {
  fetchSkills()
  loadHistory()
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
.skill-empty-state {
  width: 100%;
  min-height: 280px;
  padding: 40px var(--page-padding);
}

.skill-empty-card {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 220px;
  flex-direction: column;
  border: 1px dashed var(--gray-150);
  border-radius: 16px;
  background: linear-gradient(180deg, var(--gray-0) 0%, var(--gray-25) 100%);
  color: var(--gray-500);
  text-align: center;
}

.skill-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 14px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.skill-empty-title {
  color: var(--gray-800);
  font-size: 15px;
  font-weight: 700;
  line-height: 22px;
}

.skill-empty-desc {
  margin-top: 4px;
  color: var(--gray-500);
  font-size: 13px;
  line-height: 20px;
}

.card-wrapper {
  position: relative;

  :deep(.info-card-mini-desc) {
    display: -webkit-box;
    min-height: 36px;
    color: var(--gray-700);
    white-space: normal;
    line-clamp: 2;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  :deep(.info-card-mini .info-card-icon) {
    align-self: flex-start;
  }

  &.batch-mode {
    :deep(.info-card) {
      cursor: pointer;
      border-color: var(--gray-200);

      &:hover {
        border-color: var(--gray-300);
      }
    }

    :deep(.info-card-status),
    :deep(.info-card-mini-action) {
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }

    :deep(.info-card-mini .info-card-info) {
      padding-right: 28px;
    }
  }

  &.selected {
    :deep(.info-card) {
      border-color: var(--gray-500) !important;
      background: var(--gray-25) !important;
    }
  }

  .card-select-checkbox {
    position: absolute;
    top: 16px;
    right: 16px;
    z-index: 10;
  }
}

.skill-enabled-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 18px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    color 0.18s ease;

  &:hover,
  &:focus {
    outline: none;
    border-color: var(--gray-300);
    background: var(--gray-50);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  &.loading:disabled {
    cursor: wait;
    opacity: 1;
  }

  &.enabled {
    color: var(--color-success-700);

    .action-icon-minus {
      display: none;
    }

    &:hover,
    &:focus {
      border-color: var(--color-error-200, #ffccc7);
      background: var(--color-error-50, #fff2f0);
      color: var(--color-error-700, #cf1322);

      .action-icon-check {
        display: none;
      }

      .action-icon-minus {
        display: block;
      }
    }
  }
}

.action-icon {
  flex-shrink: 0;
}

.skill-preview-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.skill-preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.skill-preview-title-area {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.skill-preview-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--gray-50);
  color: var(--gray-600);
}

.skill-preview-title-text {
  min-width: 0;
}

.skill-preview-title {
  overflow: hidden;
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-preview-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.skill-preview-disabled-tag {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 600;
}

.skill-preview-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 8px;
  padding-top: 2px;
}

.skill-preview-body {
  min-height: 260px;
  max-height: min(56vh, 520px);
  padding: 14px 16px;
  overflow-y: auto;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-25);

  :deep(.yk-markdown-preview) {
    background: transparent;
  }
}

.skill-preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
}

.skill-preview-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.skill-preview-footer-left,
.skill-preview-footer-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.remote-install-panel {
  :deep(.install-tabs > .ant-tabs-nav .ant-tabs-nav-wrap) {
    justify-content: center;
  }

  .repo-input-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 4px;

    .repo-input-field {
      flex: 1;
      min-width: 0;
    }
  }

  .history-trigger-wrapper {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    cursor: pointer;
    outline: none;
    margin-right: -4px;
    border-radius: 4px;
    transition: background-color 0.2s ease;

    &:hover {
      background-color: var(--gray-100);
    }

    &:focus,
    &:focus-visible {
      outline: none;
    }
  }

  .history-icon-trigger {
    color: var(--gray-400);
    transition: color 0.2s ease;
    outline: none;

    &:hover {
      color: var(--gray-700);
    }

    &.has-history {
      color: var(--gray-500);

      &:hover {
        color: var(--gray-700);
      }
    }
  }

  .repo-hint-text {
    font-size: 12px;
    color: var(--gray-400);
    margin-bottom: 12px;
    line-height: 1.4;

    a {
      color: var(--gray-700);
      text-decoration: underline;
    }
  }

  .tab-content-wrapper {
    padding: 4px 0 8px 0;
  }

  .skills-list-section {
    margin-top: 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
    background: var(--gray-0);
  }

  .list-operations-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--gray-150);
    padding: 8px 10px;

    .op-buttons {
      display: flex;
      gap: 2px;

      .ant-btn {
        padding: 0 4px;
        height: auto;
        font-size: 12px;
        color: var(--gray-600);

        &:hover {
          color: var(--gray-900);
        }
      }
    }
  }

  .skills-list-viewport {
    max-height: min(42vh, 420px);
    overflow-y: auto;
    overscroll-behavior: contain;
    background: var(--gray-0);
  }

  .remote-skills-list-container {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1px;
    background: var(--gray-100);
  }

  .remote-skill-row {
    display: flex;
    align-items: center;
    min-height: 46px;
    padding: 6px 10px;
    gap: 8px;
    background: var(--gray-0);
    cursor: pointer;
    transition: background-color 0.18s ease;

    &:hover,
    &.selected {
      background: var(--gray-25);
    }

    &:focus-visible {
      outline: 2px solid var(--gray-400);
      outline-offset: -2px;
    }

    &[aria-disabled='true'] {
      cursor: not-allowed;
    }
  }

  .remote-row-checkbox {
    pointer-events: none;
  }

  .single-remote-skill-card {
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-0);
    padding: 12px;
  }

  .single-remote-skill-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-width: 0;

    .ant-tag {
      flex-shrink: 0;
      margin-inline-end: 0;
    }
  }

  .single-remote-skill-name {
    line-height: 20px;
    font-weight: 600;
    color: var(--gray-900);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .single-remote-skill-meta {
    margin-top: 2px;
    font-size: 12px;
    line-height: 18px;
    color: var(--gray-500);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .remote-skill-row-content {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;

    .skill-item-name {
      font-weight: 600;
      color: var(--gray-900);
    }

    .skill-item-desc {
      display: block;
      font-size: 12px;
      color: var(--gray-500);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  .skill-install-count {
    flex-shrink: 0;
    color: var(--gray-500);
    font-size: 11px;
  }
}

@media (max-width: 600px) {
  .remote-install-panel {
    .skills-list-viewport {
      max-height: 40vh;
      overflow-y: auto;
    }

    .remote-skills-list-container {
      grid-template-columns: 1fr;
    }
  }
}

.modal-footer-summary {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 18px;
}

.modal-footer-buttons {
  display: flex;
  margin-left: auto;
  gap: 8px;
}
</style>

<!-- NOTE: unscoped style block 用于 dropdown overlay 样式穿透 teleport -->
<style lang="less">
/* Ant Design Dropdown overlay 通过 teleport 挂载到 body，
   scoped CSS 无法穿透，因此必须使用 unscoped 样式。
   使用 .history-dropdown-menu 作为 overlayClassName 命名空间。 */
.history-dropdown-menu {
  min-width: 280px;

  .ant-dropdown-menu {
    padding: 4px;
  }

  .ant-dropdown-menu-item {
    padding: 8px 12px;
    border-radius: 6px;

    .ant-dropdown-menu-title-content {
      display: flex;
      align-items: center;
      width: 100%;
    }
  }
}

/* 历史记录行：仓库地址 + 删除按钮 */
.history-item-menu-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: 12px;

  .history-item-text {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    color: var(--gray-800);
    line-height: 1;
  }

  .history-item-del-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    color: var(--gray-400);
    cursor: pointer;
    border-radius: 4px;
    transition: all 0.2s ease;
    flex-shrink: 0;

    svg {
      display: block;
    }

    &:hover {
      color: var(--color-error-500, #ff4d4f);
      background: var(--color-error-10, rgba(255, 77, 79, 0.1));
    }
  }
}

.history-empty-text {
  color: var(--gray-400);
  font-size: 12px;
}

/* 清空历史记录按钮内容 — 图标在左文字在右，水平居中 */
.clear-history-btn-content {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--color-error-500, #ff4d4f);
  font-weight: 500;
  font-size: 13px;
  width: 100%;

  .clear-icon {
    display: flex;
    align-items: center;
  }
}
</style>
