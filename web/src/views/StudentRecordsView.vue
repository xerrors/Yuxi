<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Plus, RefreshCw } from '@lucide/vue'
import { message } from 'ant-design-vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import { counselingApi } from '@/apis/counseling_api'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const canAssign = computed(() => userStore.businessRoles.includes('business_admin'))
const canManage = computed(() => userStore.businessRoles.includes('counselor'))
const studentId = computed(() => route.params.studentId)
const students = ref([])
const counselors = ref([])
const detail = ref(null)
const loading = ref(false)
const error = ref('')
const counselorError = ref('')
const counselorsLoading = ref(false)
const search = ref('')
const createOpen = ref(false)
const saving = ref(false)
const createForm = ref({ student_code: '', counselor_id: undefined })
const editForm = ref({ background_summary: '', status: 'active' })

const visibleStudents = computed(() => {
  const query = search.value.trim().toLowerCase()
  return query
    ? students.value.filter((item) => item.student_code.toLowerCase().includes(query))
    : students.value
})

const counselorName = (id) =>
  counselors.value.find((item) => item.id === id)?.username ||
  (id === userStore.userId ? userStore.username : `用户 ${id}`)
const canOpen = (student) => canManage.value && student.counselor_id === userStore.userId

async function loadStudents() {
  loading.value = true
  error.value = ''
  try {
    students.value = await counselingApi.listStudents()
  } catch (cause) {
    error.value = cause.message || '加载学生档案失败'
  } finally {
    loading.value = false
  }
}

async function loadCounselors() {
  if (!canAssign.value) return
  counselorsLoading.value = true
  counselorError.value = ''
  counselors.value = []
  try {
    counselors.value = await counselingApi.listCounselors()
  } catch (cause) {
    counselorError.value = cause.message || '加载负责人失败'
  } finally {
    counselorsLoading.value = false
  }
}

async function loadDetail(id) {
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    detail.value = await counselingApi.getStudent(id)
    editForm.value = {
      background_summary: detail.value.background_summary,
      status: detail.value.status
    }
  } catch (cause) {
    error.value = cause.message || '加载档案详情失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.value = { student_code: '', counselor_id: undefined }
  createOpen.value = true
  void loadCounselors()
}

async function createStudent() {
  const code = createForm.value.student_code.trim()
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(code)) {
    message.error('学生编号须为 1–64 位字母、数字、下划线或连字符')
    return
  }
  if (!createForm.value.counselor_id) {
    message.error('请选择负责人')
    return
  }
  saving.value = true
  try {
    await counselingApi.createStudent({
      student_code: code,
      counselor_id: createForm.value.counselor_id
    })
    createOpen.value = false
    message.success('学生档案已创建')
    await loadStudents()
  } catch (cause) {
    message.error(cause.message || '创建档案失败')
  } finally {
    saving.value = false
  }
}

async function saveDetail() {
  saving.value = true
  try {
    detail.value = await counselingApi.updateStudent(studentId.value, editForm.value)
    message.success('档案已保存')
  } catch (cause) {
    message.error(cause.message || '保存档案失败')
  } finally {
    saving.value = false
  }
}

watch(
  studentId,
  (id) => {
    if (id) void loadDetail(id)
    else {
      void loadStudents()
      void loadCounselors()
    }
  },
  { immediate: true }
)
</script>

<template>
  <div class="student-records-view">
    <PageHeader
      :title="studentId ? '学生档案详情' : '学生档案'"
      :loading="loading || saving"
      :show-border="true"
    >
      <template #actions>
        <a-button v-if="studentId" @click="router.push('/students')">
          <template #icon><ArrowLeft :size="16" /></template>
          返回列表
        </a-button>
        <template v-else>
          <a-button :disabled="loading" @click="loadStudents">
            <template #icon><RefreshCw :size="16" /></template>
            刷新
          </a-button>
          <a-button v-if="canAssign" type="primary" @click="openCreate">
            <template #icon><Plus :size="16" /></template>
            新建档案
          </a-button>
        </template>
      </template>
    </PageHeader>

    <main class="student-content">
      <a-alert v-if="error" type="error" show-icon :message="error" class="state-alert">
        <template #description>
          <a-button size="small" @click="studentId ? loadDetail(studentId) : loadStudents()"
            >重试</a-button
          >
        </template>
      </a-alert>
      <template v-else-if="studentId">
        <a-skeleton v-if="loading" active />
        <section v-else-if="detail" class="detail-card">
          <div class="detail-heading">
            <div>
              <span class="eyebrow">学生编号</span>
              <h2>{{ detail.student_code }}</h2>
            </div>
            <a-tag :color="detail.status === 'active' ? 'green' : 'default'">
              {{ detail.status === 'active' ? '进行中' : '已结案' }}
            </a-tag>
          </div>
          <a-form layout="vertical" @finish="saveDetail">
            <a-form-item label="背景摘要">
              <a-textarea
                v-model:value="editForm.background_summary"
                :rows="9"
                :maxlength="10000"
                show-count
                placeholder="记录当前辅导所需的背景"
              />
            </a-form-item>
            <a-form-item label="档案状态">
              <a-select v-model:value="editForm.status" class="status-select">
                <a-select-option value="active">进行中</a-select-option>
                <a-select-option value="closed">已结案</a-select-option>
              </a-select>
            </a-form-item>
            <a-button type="primary" html-type="submit" :loading="saving">保存档案</a-button>
          </a-form>
        </section>
      </template>
      <template v-else>
        <div class="list-intro">
          <div>
            <h2>{{ canAssign ? '本部门档案' : '我负责的档案' }}</h2>
            <p>
              {{
                canAssign
                  ? '创建档案时指定初始负责人；背景内容由负责人维护。'
                  : '查看并维护分配给你的学生档案。'
              }}
            </p>
          </div>
          <a-input
            v-model:value="search"
            allow-clear
            placeholder="按学生编号查找"
            class="search-input"
            aria-label="按学生编号查找"
          />
        </div>
        <a-skeleton v-if="loading" active />
        <a-empty
          v-else-if="visibleStudents.length === 0"
          :description="search ? '没有匹配的档案' : '暂无学生档案'"
        >
          <a-button v-if="canAssign && !search" type="primary" @click="openCreate"
            >新建档案</a-button
          >
        </a-empty>
        <div v-else class="student-table-wrap">
          <table class="student-table">
            <thead>
              <tr>
                <th>学生编号</th>
                <th>状态</th>
                <th>负责人</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="student in visibleStudents" :key="student.id">
                <td class="student-code">{{ student.student_code }}</td>
                <td>
                  <a-tag :color="student.status === 'active' ? 'green' : 'default'">{{
                    student.status === 'active' ? '进行中' : '已结案'
                  }}</a-tag>
                </td>
                <td>{{ counselorName(student.counselor_id) }}</td>
                <td>
                  <RouterLink v-if="canOpen(student)" :to="`/students/${student.id}`"
                    >查看详情</RouterLink
                  ><span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </main>

    <a-modal
      v-model:open="createOpen"
      title="新建学生档案"
      :confirm-loading="saving"
      ok-text="创建档案"
      @ok="createStudent"
    >
      <a-form layout="vertical">
        <a-form-item label="学生编号" required>
          <a-input
            v-model:value="createForm.student_code"
            :maxlength="64"
            placeholder="输入部门内唯一编号"
          />
        </a-form-item>
        <a-form-item label="负责人" required>
          <a-select
            v-model:value="createForm.counselor_id"
            :loading="counselorsLoading"
            placeholder="选择本部门辅导人员"
            show-search
            option-filter-prop="label"
          >
            <a-select-option
              v-for="person in counselors"
              :key="person.id"
              :value="person.id"
              :label="person.username"
              >{{ person.username }}</a-select-option
            >
          </a-select>
          <a-alert
            v-if="counselorError"
            type="error"
            :message="counselorError"
            class="counselor-error"
          >
            <template #description
              ><a-button size="small" @click="loadCounselors">重试</a-button></template
            >
          </a-alert>
          <p v-else-if="!counselorsLoading && counselors.length === 0" class="candidate-empty">
            本部门暂无可分配的辅导人员
          </p>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.student-records-view {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}
.student-content {
  max-width: 1040px;
  margin: 0 auto;
  padding: 32px var(--page-padding);
}
.state-alert {
  margin-bottom: 20px;
}
.list-intro {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 20px;
  margin-bottom: 24px;
}
.list-intro h2,
.detail-heading h2 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 600;
}
.list-intro p {
  margin: 0;
  color: var(--gray-600);
}
.search-input {
  width: 240px;
}
.student-table-wrap,
.detail-card {
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
}
.student-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
.student-table th,
.student-table td {
  padding: 15px 20px;
  border-bottom: 1px solid var(--gray-100);
}
.student-table th {
  color: var(--gray-600);
  background: var(--gray-25);
  font-weight: 500;
}
.student-table tr:last-child td {
  border-bottom: 0;
}
.student-code {
  font-weight: 600;
}
.muted,
.eyebrow {
  color: var(--gray-500);
}
.detail-card {
  max-width: 720px;
  padding: 28px;
}
.detail-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.eyebrow {
  font-size: 12px;
}
.status-select {
  width: 180px;
}
.counselor-error {
  margin-top: 8px;
}
.candidate-empty {
  margin: 8px 0 0;
  color: var(--gray-600);
}
@media (max-width: 640px) {
  .student-content {
    padding-top: 20px;
  }
  .list-intro {
    display: block;
  }
  .search-input {
    width: 100%;
    margin-top: 16px;
  }
  .student-table-wrap {
    overflow-x: auto;
  }
  .student-table {
    min-width: 560px;
  }
  .detail-card {
    padding: 20px;
  }
}
</style>
