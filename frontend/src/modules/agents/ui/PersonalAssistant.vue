<template>
  <section class="personal-assistant" :class="{ 'is-setup': !loading && !loadError && !assistant }">
    <div v-if="loading" class="personal-message" role="status">正在加载个人助理…</div>
    <div v-else-if="loadError" class="personal-message" role="alert">
      <p>{{ loadError }}</p>
      <a-button @click="loadAssistant">重试</a-button>
    </div>
    <div v-else-if="!assistant" class="personal-setup">
      <form class="personal-form" @submit.prevent="createAssistant">
        <div class="setup-heading">
          <FallbackAvatar kind="agent" :name="form.name" seed="personal-assistant" :src="form.icon" :size="88" />
          <h2>创建你的个人助理</h2>
          <p>为助理选择名字和形象，之后随时可以修改。</p>
        </div>
        <div class="form-field">
          <label for="personal-name">助理名称</label>
          <a-input id="personal-name" v-model:value="form.name" :maxlength="80" placeholder="给助理起个名字" :disabled="saving" size="large" />
        </div>
        <div class="form-field">
          <span class="field-label">选择头像</span>
          <div class="avatar-choices" role="group" aria-label="选择头像">
            <button v-for="avatar in avatars" :key="avatar" type="button" class="avatar-choice" :class="{ selected: form.icon === avatar }" :aria-label="`选择头像 ${avatars.indexOf(avatar) + 1}`" :aria-pressed="form.icon === avatar" :disabled="saving || uploading" @click="form.icon = avatar">
              <FallbackAvatar kind="agent" :src="avatar" :size="54" decorative />
            </button>
            <a-upload :show-upload-list="false" :before-upload="uploadAvatar" accept="image/*" :disabled="saving || uploading">
              <button type="button" class="avatar-upload" :disabled="saving || uploading" aria-label="上传头像"><Upload :size="18" aria-hidden="true" /><span>上传</span></button>
            </a-upload>
          </div>
        </div>
        <p v-if="formError" class="personal-error" role="alert">{{ formError }}</p>
        <a-button type="primary" html-type="submit" size="large" :loading="saving" :disabled="uploading">创建助理 <ArrowRight :size="16" aria-hidden="true" /></a-button>
      </form>
    </div>
    <template v-else>
      <div v-if="!modelAvailable" class="personal-model-notice" role="alert"><CircleAlert :size="15" aria-hidden="true" /><span>{{ modelError || '个人助理模型不可用，请联系管理员。' }}</span></div>
      <SessionWorkspace
        ref="workspaceRef"
        class="personal-workspace"
        personal-mode
        :personal-profile-open="profileOpen"
        :agent-id="assistant.agent_id"
        :is-new-session="false"
        :send-disabled="!modelAvailable"
        @status-change="sessionStatus = $event"
      >
        <template #personal-actions="{ sessions, error, refresh, actionsTarget, profileDockTarget }">
          <Teleport v-if="actionsTarget" :to="actionsTarget">
            <button type="button" class="personal-action-button" aria-label="查看助理完整轨迹" title="查看助理完整轨迹" @click="workspaceRef?.navigateToSession(assistant.thread_id)"><ListTree :size="18" aria-hidden="true" /></button>
            <button ref="profileButtonRef" type="button" class="lucide-icon-btn personal-profile-button" :class="{ active: profileOpen }" aria-label="个人助理资料" title="个人助理资料" aria-haspopup="dialog" :aria-expanded="profileOpen" :aria-controls="profileOpen ? profileId : undefined" @click="profileOpen = !profileOpen" @keydown.esc.stop.prevent="closeProfile">
              <FallbackAvatar kind="agent" :name="assistant.name" :seed="assistant.id" :src="assistant.icon || ''" :size="28" decorative />
            </button>
          </Teleport>
          <Teleport v-if="actionsTarget && profileOpen" :to="profileDockTarget || actionsTarget">
            <div ref="profileCardRef" :id="profileId" class="personal-profile" :class="{ 'is-floating': !profileDockTarget }" role="dialog" aria-label="个人助理资料" @vue:before-update="rememberProfileFocus" @vue:updated="restoreProfileFocus" @keydown.esc.stop.prevent="closeProfile">
              <div class="personal-profile-header">
                <div class="personal-identity">
                  <FallbackAvatar kind="agent" :name="assistant.name" :seed="assistant.id" :src="assistant.icon || ''" :size="48" decorative />
                  <div class="personal-heading">
                    <strong :title="assistant.name">{{ assistant.name }}</strong>
                    <span>{{ !modelAvailable && sessionStatus === '对话已就绪' ? '暂不可发送' : sessionStatus }}</span>
                  </div>
                </div>
                <button ref="profileEditRef" type="button" class="lucide-icon-btn personal-edit-button" aria-label="编辑资料" title="编辑资料" @click="openEditor"><Pencil :size="16" aria-hidden="true" /></button>
              </div>
              <PersonalTaskList :sessions="sessions" :error="error" @open="openTask" @refresh="refresh" />
            </div>
          </Teleport>
        </template>
        <template #personal-empty>
          <div class="empty-conversation">
            <h2>和 {{ assistant.name }} 开始对话</h2>
            <p>聊聊你的想法，或告诉它你需要什么帮助。</p>
          </div>
        </template>
      </SessionWorkspace>
      <a-modal v-model:open="editing" title="编辑个人助理" :confirm-loading="saving" :after-close="closeProfile" ok-text="保存" cancel-text="取消" @ok="saveAssistant">
        <div class="personal-edit">
          <div class="edit-identity">
            <FallbackAvatar kind="agent" :name="form.name" :seed="assistant.id" :src="form.icon" :size="72" />
            <div><strong>{{ form.name.trim() || '你的助理' }}</strong><p>选择一个喜欢的形象</p></div>
          </div>
          <div class="avatar-choices" role="group" aria-label="选择头像">
            <button v-for="avatar in avatars" :key="avatar" type="button" class="avatar-choice" :class="{ selected: form.icon === avatar }" :aria-label="`选择头像 ${avatars.indexOf(avatar) + 1}`" :aria-pressed="form.icon === avatar" :disabled="saving || uploading" @click="form.icon = avatar">
              <FallbackAvatar kind="agent" :src="avatar" :size="48" decorative />
            </button>
            <a-upload :show-upload-list="false" :before-upload="uploadAvatar" accept="image/*" :disabled="saving || uploading">
              <button type="button" class="avatar-upload" :disabled="saving || uploading" aria-label="上传头像"><Upload :size="18" aria-hidden="true" /><span>上传</span></button>
            </a-upload>
          </div>
          <div class="form-field">
            <label for="personal-edit-name">助理名称</label>
            <a-input id="personal-edit-name" v-model:value="form.name" :maxlength="80" :disabled="saving" />
          </div>
          <div class="form-field">
            <label for="personal-prompt">交流偏好</label>
            <a-textarea id="personal-prompt" v-model:value="form.context.system_prompt" :rows="4" :disabled="saving" placeholder="例如：请用简洁的语言回答，先说明结论。" />
            <p class="personal-hint">描述你希望助理如何与你交流。</p>
          </div>
          <p v-if="formError" class="personal-error" role="alert">{{ formError }}</p>
        </div>
      </a-modal>
    </template>
  </section>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref, useId, watch } from 'vue'
import { ArrowRight, CircleAlert, Pencil, Upload, ListTree } from '@lucide/vue'
import { useRoute } from 'vue-router'
import { agentApi } from '@/apis/agent_api'
import { userApi } from '@/apis/user_api'
import { MAX_IMAGE_UPLOAD_SIZE_BYTES, MAX_IMAGE_UPLOAD_SIZE_MB } from '@/shared/lib/upload_limits'
import { message } from 'ant-design-vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import SessionWorkspace from '@/modules/session/ui/SessionWorkspace.vue'
import PersonalTaskList from '@/modules/session/ui/PersonalTaskList.vue'

const route = useRoute()
const avatars = ['orbit', 'leaf', 'sun', 'tide', 'dusk'].map((name) => `/icons/personal-assistant/${name}.svg`)
const assistant = ref(null)
const modelAvailable = ref(false)
const modelError = ref('')
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const uploading = ref(false)
const editing = ref(false)
const profileOpen = ref(false)
const profileId = useId()
const profileButtonRef = ref(null)
const profileCardRef = ref(null)
const profileEditRef = ref(null)
let retainedProfileFocus = null
const formError = ref('')
const workspaceRef = ref(null)
const sessionStatus = ref('正在加载对话')
const form = reactive({ name: '', icon: avatars[0], context: { system_prompt: '' } })

/** 从服务端结果恢复唯一主会话和模型状态。 */
async function applyResult(result) {
  loading.value = false
  assistant.value = result.assistant
  modelAvailable.value = result.model_available === true
  modelError.value = result.model_error || ''
  if (assistant.value) {
    await nextTick()
    const opened = await workspaceRef.value?.selectThreadFromRoute(assistant.value.thread_id)
    if (!opened) loadError.value = '对话加载失败，请重试。'
    else await openTraceFromRoute(route.query.session_id)
  }
}

/** 读取助理，失败时保留可重试的错误状态。 */
async function loadAssistant() {
  loading.value = true
  loadError.value = ''
  try {
    const result = await agentApi.getPersonalAssistant()
    loading.value = false
    await applyResult(result)
  } catch (error) {
    loadError.value = error.message || '个人助理加载失败'
  } finally {
    loading.value = false
  }
}

/** 上传图片并等待用户保存资料。 */
async function uploadAvatar(file) {
  if (!file.type.startsWith('image/') || file.size > MAX_IMAGE_UPLOAD_SIZE_BYTES) {
    formError.value = `请选择不超过 ${MAX_IMAGE_UPLOAD_SIZE_MB}MB 的图片。`
    return false
  }
  uploading.value = true
  formError.value = ''
  try {
    const result = await userApi.uploadImage(file)
    form.icon = result.image_url || result.url || ''
    if (!form.icon) throw new Error('上传未返回头像地址')
  } catch (error) {
    formError.value = error.message || '头像上传失败'
  } finally {
    uploading.value = false
  }
  return false
}

/** 创建后以服务端返回的身份打开主会话。 */
async function createAssistant() {
  if (!form.name.trim()) {
    formError.value = '请输入助理名称。'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    await applyResult(await agentApi.createPersonalAssistant({ name: form.name.trim(), avatar: form.icon }))
  } catch (error) {
    formError.value = error.message || '创建失败，请重试。'
  } finally {
    saving.value = false
  }
}

/** 浮层点击外部关闭；平铺卡片保留到用户主动收起。 */
function handleProfileOutside(event) {
  const card = profileCardRef.value
  if (!profileOpen.value || !card?.classList.contains('is-floating')) return
  const path = event.composedPath()
  if (!path.includes(card) && !path.includes(profileButtonRef.value)) profileOpen.value = false
}

/** 卡片移动前只记录内部焦点，正文输入持焦时不接管。 */
function rememberProfileFocus() {
  const focused = document.activeElement
  retainedProfileFocus = profileCardRef.value?.contains(focused) ? focused : null
}

/** Teleport 移动同一张卡片后恢复仍存在的控件。 */
async function restoreProfileFocus() {
  const focused = retainedProfileFocus
  retainedProfileFocus = null
  // 挂载目标的 v-show 在同一轮更新稍后生效，等待它可见再恢复。
  await nextTick()
  if (focused?.isConnected && document.activeElement === document.body) {
    focused.focus({ preventScroll: true })
  }
}

/** 切换挂载位置后即使焦点离开卡片，也能用 Escape 关闭。 */
function handleProfileKeydown(event) {
  if (event.key === 'Escape' && profileOpen.value && profileCardRef.value?.getClientRects().length) {
    event.preventDefault()
    closeProfile()
  }
}

watch(profileOpen, async (open) => {
  if (!open) return
  await nextTick()
  profileEditRef.value?.focus()
})

/** 关闭资料卡片并返回页头入口。 */
function closeProfile() {
  profileOpen.value = false
  profileButtonRef.value?.focus()
}

/** 从资料卡片打开任务轨迹，避免浮层遮挡右侧面板。 */
function openTask(sessionId, title) {
  closeProfile()
  workspaceRef.value?.navigateToSession(sessionId, title)
}

/** 复制当前资料到编辑草稿。 */
function openEditor() {
  profileOpen.value = false
  Object.assign(form, { name: assistant.value.name, icon: assistant.value.icon || avatars[0], context: { system_prompt: assistant.value.config_json?.context?.system_prompt || '' } })
  formError.value = ''
  editing.value = true
}

/** 保存名称、头像和交流偏好。 */
async function saveAssistant() {
  if (!form.name.trim()) {
    formError.value = '请输入助理名称。'
    return
  }
  if (uploading.value) return
  saving.value = true
  formError.value = ''
  try {
    const result = await agentApi.updatePersonalAssistant({ name: form.name.trim(), icon: form.icon, config_json: { context: { ...form.context } } })
    assistant.value = result.assistant
    modelAvailable.value = result.model_available === true
    modelError.value = result.model_error || ''
    editing.value = false
  } catch (error) {
    formError.value = error.message || '保存失败，请重试。'
  } finally {
    saving.value = false
  }
}

/** 轨迹读取失败不遮蔽仍可使用的主对话。 */
async function openTraceFromRoute(sessionId) {
  try {
    await workspaceRef.value?.openSessionFromRoute(typeof sessionId === 'string' ? sessionId : '')
  } catch (error) {
    message.error(error.message || '轨迹加载失败')
  }
}

watch(() => route.query.session_id, (sessionId) => {
  if (assistant.value && !loading.value) void openTraceFromRoute(sessionId)
})
onMounted(loadAssistant)
onMounted(() => document.addEventListener('pointerdown', handleProfileOutside, true))
onUnmounted(() => document.removeEventListener('pointerdown', handleProfileOutside, true))
onMounted(() => document.addEventListener('keydown', handleProfileKeydown))
onUnmounted(() => document.removeEventListener('keydown', handleProfileKeydown))
</script>

<style scoped>
.personal-assistant { display: flex; flex-direction: column; height: 100%; min-height: 0; color: var(--color-text); background: var(--bg-page); }
.personal-assistant.is-setup { overflow-y: auto; }
.personal-message { margin: auto; padding: 32px; text-align: center; }
.personal-setup { flex: 0 0 auto; width: min(440px, calc(100% - 48px)); margin: auto; padding: 48px 0; }
.personal-form, .personal-edit, .form-field { display: flex; flex-direction: column; align-items: stretch; }
.personal-form { gap: 24px; min-width: 0; }
.setup-heading { display: flex; flex-direction: column; align-items: center; text-align: center; padding-bottom: 8px; }
.setup-heading h2 { margin: 20px 0 6px; font-size: 22px; line-height: 1.35; }
.setup-heading p, .edit-identity p, .empty-conversation p { color: var(--color-text-secondary); line-height: 1.6; }
.setup-heading p { margin: 0; font-size: 14px; }
.form-field { gap: 8px; }
.form-field label, .field-label { font-size: 13px; font-weight: 600; }
.personal-action-button { display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 0; border-radius: 9px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.personal-action-button:hover { background: var(--gray-50); color: var(--color-text); }
.personal-action-button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
.personal-profile-button { width: 36px; height: 36px; padding: 3px; border: 1px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
.personal-profile-button:hover, .personal-profile-button.active { border-color: var(--border-surface-hover); background: var(--gray-50); }
.personal-profile { box-sizing: border-box; width: 320px; max-height: 100%; overflow-y: auto; padding: 20px; border: 1px solid var(--border-surface); border-radius: 16px; background: var(--gray-25); color: var(--color-text); pointer-events: auto; }
.personal-profile.is-floating { position: absolute; top: calc(100% + 12px); right: 0; width: min(320px, var(--personal-profile-max-width)); max-height: calc(100dvh - 112px); box-shadow: 0 8px 24px var(--shadow-1); }
.personal-profile-header { display: flex; align-items: center; gap: 12px; }
.personal-identity { display: flex; flex: 1; min-width: 0; align-items: center; gap: 12px; }
.personal-heading { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.personal-heading strong { font-size: 16px; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.personal-heading span { color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.personal-edit-button { flex: 0 0 auto; width: 32px; height: 32px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.personal-edit-button:hover { background: var(--gray-50); color: var(--color-text); }
.personal-model-notice { display: flex; gap: 7px; align-items: center; width: min(720px, calc(100% - 48px)); margin: 12px auto 0; color: var(--color-warning-900); font-size: 13px; line-height: 1.5; }
.personal-model-notice svg { flex: 0 0 auto; }
.personal-workspace { flex: 1; min-height: 0; }
.empty-conversation { display: flex; flex-direction: column; align-items: center; }
.empty-conversation h2 { margin: 0 0 6px; color: var(--color-text); font-size: 20px; line-height: 1.4; }
.empty-conversation p { margin: 0; font-size: 14px; }
.personal-edit { gap: 20px; padding: 12px 0 4px; }
.edit-identity { display: flex; align-items: center; gap: 16px; }
.edit-identity strong { font-size: 16px; }
.edit-identity p { margin: 4px 0 0; font-size: 13px; }
.avatar-choices { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.avatar-choice { display: flex; padding: 3px; border: 2px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
.avatar-choice.selected { border-color: var(--main-color); }
.avatar-upload { width: 58px; height: 58px; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 2px; border: 1px dashed var(--gray-300); border-radius: 50%; background: var(--gray-10); color: var(--color-text-secondary); cursor: pointer; font-size: 11px; }
.avatar-upload:hover { border-color: var(--main-color); color: var(--main-color); }
.avatar-choice:focus-visible, .avatar-upload:focus-visible, .personal-edit-button:focus-visible, .personal-profile-button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
.avatar-choice:disabled, .avatar-upload:disabled { cursor: not-allowed; opacity: .55; }
.personal-error { color: var(--color-error-700); margin: 0; }
.personal-hint { color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; margin: 0; }
@media (max-height: 700px) {
  .personal-setup { margin: 0 auto; padding: 32px 0; }
}
</style>
