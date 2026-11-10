<template>
  <section class="url-import-settings settings-section-block" aria-labelledby="url-import-settings-title">
    <h3 id="url-import-settings-title" class="settings-section-title">知识库 URL 导入</h3>
    <div class="settings-surface-card">
      <a-spin v-if="loading" tip="正在加载配置…" />
      <a-alert v-else-if="loadError" type="error" :message="loadError" show-icon>
        <template #action><a-button size="small" @click="loadOption">重新加载</a-button></template>
      </a-alert>
      <template v-else>
        <div class="settings-header">
          <div class="settings-header-copy">
            <h4 class="settings-form-label"><label for="knowledge-url-allowed-hosts">网页域名白名单</label></h4>
            <p class="settings-form-description">允许导入列表中的域名及其子域名，仅用于知识库网页导入。</p>
          </div>
          <a-button v-if="!editing" type="text" size="small" @click="startEditing">
            <template #icon><Pencil :size="14" /></template>
            编辑
          </a-button>
          <div v-else class="settings-header-actions">
            <a-button size="small" :disabled="saving" @click="editing = false">取消</a-button>
            <a-button type="primary" size="small" :loading="saving" @click="saveOption">保存</a-button>
          </div>
        </div>
        <template v-if="editing">
          <a-select
            v-model:value="draftHosts"
            id="knowledge-url-allowed-hosts"
            mode="tags"
            style="width: 100%"
            :disabled="saving"
            :token-separators="[',', ' ', '\n']"
            :options="[]"
            placeholder="输入域名后按回车，例如 docs.example.com"
          />
          <p class="settings-form-description">填写域名，无需协议或路径。清空并保存会关闭 URL 导入，保存后对新请求生效。</p>
        </template>
        <div v-else-if="hosts.length" class="settings-header-actions" aria-label="已允许导入的域名">
          <a-tag v-for="host in hosts" :key="host">{{ host }}</a-tag>
        </div>
        <p v-else class="settings-form-description">未配置域名，URL 导入已关闭</p>
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Pencil } from '@lucide/vue'
import { configOptionsApi } from '@/apis/system_api'

const OPTION_KEY = 'knowledge_url_import_policy'
const option = ref(null)
const hosts = computed(() => option.value?.effective_value?.allowed_hosts || [])
const draftHosts = ref([])
const loading = ref(true)
const loadError = ref('')
const editing = ref(false)
const saving = ref(false)

/** 加载后端解析的有效域名，包含尚未被页面覆盖的部署配置。 */
const loadOption = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const data = await configOptionsApi.getOptions()
    option.value = data.options.find((item) => item.key === OPTION_KEY)
    if (!option.value) throw new Error('URL 导入配置暂不可用，请重新加载')
  } catch (error) {
    loadError.value = error.message || '加载 URL 导入配置失败'
  } finally {
    loading.value = false
  }
}

/** 编辑草稿不改变当前生效的白名单。 */
const startEditing = () => {
  draftHosts.value = [...hosts.value]
  editing.value = true
}

/** 保存后采用服务端值；失败时保留草稿供修正或重试。 */
const saveOption = async () => {
  saving.value = true
  try {
    const data = await configOptionsApi.updateOption(OPTION_KEY, { allowed_hosts: draftHosts.value })
    option.value = data.option
    editing.value = false
    message.success('URL 导入配置已保存')
  } catch (error) {
    message.error(error.message || '保存 URL 导入配置失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadOption)
</script>
