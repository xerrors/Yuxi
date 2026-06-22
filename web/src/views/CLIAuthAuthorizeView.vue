<template>
  <main class="cli-auth-view">
    <section class="cli-auth-panel">
      <div class="cli-auth-header">
        <p class="eyebrow">Yuxi CLI</p>
        <h1>确认命令行登录</h1>
      </div>

      <a-alert v-if="errorMessage" type="error" :message="errorMessage" show-icon />

      <a-spin v-else-if="loading" />

      <template v-else>
        <a-result
          v-if="approved"
          status="success"
          title="已授权"
          sub-title="可以关闭此页面并回到终端。"
        />

        <div v-else class="session-summary">
          <div class="code-block">{{ userCode }}</div>
          <a-alert
            type="warning"
            show-icon
            message="请确认这是你本人发起的命令行登录"
            description="确认后将以你当前的身份创建一个 API Key 并返回给终端。若不是你本人发起，请勿确认并关闭此页面。"
          />
          <dl>
            <div>
              <dt>凭据名称</dt>
              <dd>{{ session?.key_name || 'Yuxi CLI' }}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>{{ session?.status || '-' }}</dd>
            </div>
            <div>
              <dt>过期时间</dt>
              <dd>{{ session?.expires_at || '-' }}</dd>
            </div>
          </dl>
          <a-button type="primary" size="large" :loading="approving" @click="approveSession">
            确认授权
          </a-button>
        </div>
      </template>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { authApi } from '@/apis/auth_api'

const route = useRoute()
const loading = ref(true)
const approving = ref(false)
const approved = ref(false)
const errorMessage = ref('')
const session = ref(null)

const userCode = computed(() => String(route.query.user_code || '').trim().toUpperCase())

async function loadSession() {
  if (!userCode.value) {
    errorMessage.value = '缺少 CLI 授权码'
    loading.value = false
    return
  }
  try {
    loading.value = true
    session.value = await authApi.getCLIAuthSession(userCode.value)
  } catch (error) {
    errorMessage.value = error.message || '获取 CLI 授权会话失败'
  } finally {
    loading.value = false
  }
}

async function approveSession() {
  try {
    approving.value = true
    await authApi.approveCLIAuthSession(userCode.value)
    approved.value = true
  } catch (error) {
    errorMessage.value = error.message || '确认 CLI 授权失败'
  } finally {
    approving.value = false
  }
}

onMounted(loadSession)
</script>

<style scoped lang="less">
.cli-auth-view {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px 16px;
  background: var(--gray-50);
}

.cli-auth-panel {
  width: min(520px, 100%);
  padding: 32px;
  border: 1px solid var(--dark-10);
  border-radius: 8px;
  background: var(--color-bg-container);
}

.cli-auth-header {
  margin-bottom: 24px;

  .eyebrow {
    margin: 0 0 8px;
    color: var(--color-text-secondary);
    font-size: 13px;
  }

  h1 {
    margin: 0;
    color: var(--color-text);
    font-size: 26px;
    font-weight: 650;
  }
}

.session-summary {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.code-block {
  padding: 16px;
  border: 1px solid var(--dark-10);
  border-radius: 8px;
  background: var(--color-bg-elevated);
  color: var(--color-text);
  font-size: 24px;
  font-weight: 650;
  letter-spacing: 0;
  text-align: center;
}

dl {
  display: grid;
  gap: 12px;
  margin: 0;

  div {
    display: grid;
    grid-template-columns: 88px 1fr;
    gap: 16px;
  }

  dt {
    color: var(--color-text-secondary);
  }

  dd {
    margin: 0;
    color: var(--color-text);
    overflow-wrap: anywhere;
  }
}

@media (max-width: 560px) {
  .cli-auth-panel {
    padding: 24px;
  }

  dl div {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
