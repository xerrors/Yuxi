<template>
  <main class="global-trash">
    <PageHeader title="统一回收站">
      <template #actions>
        <a-button :loading="loading" @click="load">刷新知识库列表</a-button>
      </template>
    </PageHeader>
    <a-alert
      type="info"
      show-icon
      message="知识文档、个人文件与正式会话附件统一回收"
      description="删除后保留30天，到期自动清理；恢复保持原位置且不覆盖同名文件。知识文档按管理权限显示，个人文件与附件仅本人可见。未确认上传仍按临时文件规则处理。"
    />
    <PersonalTrashTable />
    <h2>可管理的知识库文档</h2>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-spin v-else-if="loading" />
    <a-empty
      v-else-if="!databases.length"
      description="暂无可管理的托管知识库（不代表其他资源没有已删除内容）"
    />
    <section
      v-for="database in databases"
      :key="`${generation}:${database.kb_id}`"
      class="trash-source"
    >
      <h2>原知识库：{{ database.name }}</h2>
      <TrashDrawer :kb-id="database.kb_id" :open="true" embedded />
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { databaseApi } from '@/apis/knowledge_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import TrashDrawer from '@/components/knowledge/TrashDrawer.vue'
import PersonalTrashTable from '@/components/workspace/PersonalTrashTable.vue'

const databases = ref([])
const loading = ref(false)
const error = ref('')
const generation = ref(0)
async function load() {
  const request = ++generation.value
  loading.value = true
  error.value = ''
  databases.value = []
  try {
    const result = await databaseApi.getTrashDatabases()
    if (request !== generation.value) return
    databases.value = result.databases
  } catch (err) {
    if (request === generation.value) error.value = err.message || '知识库列表加载失败，请刷新重试'
  } finally {
    if (request === generation.value) loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.global-trash {
  padding: 24px;
  overflow: auto;
  height: 100%;
}
.global-trash > :not(:first-child) {
  margin-top: 16px;
}
.trash-source {
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 16px;
}
h2 {
  font-size: 16px;
  margin: 0 0 12px;
}
</style>
