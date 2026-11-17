<template>
  <main class="provider-view">
    <section class="provider-panel" aria-live="polite">
      <h1>使用 Yuxi 登录</h1>
      <template v-if="errorMessage">
        <a-alert type="error" :message="errorMessage" show-icon />
        <a-button @click="authorize">重试</a-button>
      </template>
      <template v-else>
        <a-spin />
        <p>正在确认身份并返回应用…</p>
      </template>
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { oidcProviderApi } from '@/apis/oidc_provider_api'

const route = useRoute()
const errorMessage = ref('')
const fields = [
  'client_id',
  'redirect_uri',
  'response_type',
  'scope',
  'state',
  'nonce',
  'code_challenge',
  'code_challenge_method'
]

/** 仅转发授权参数，回调合法性由身份服务再次验证。 */
async function authorize() {
  errorMessage.value = ''
  try {
    const parameters = Object.fromEntries(
      fields
        .filter((key) => typeof route.query[key] === 'string')
        .map((key) => [key, route.query[key]])
    )
    const result = await oidcProviderApi.authorize(parameters)
    const target = new URL(result.redirect_url)
    if (!['https:', 'http:'].includes(target.protocol) || target.username || target.password) {
      throw new Error('登录返回地址无效，请重新发起登录')
    }
    window.location.assign(target.href)
  } catch (error) {
    errorMessage.value = error.message || '登录确认失败，请重试'
  }
}

onMounted(authorize)
</script>

<style scoped lang="less">
.provider-view {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--gray-50);
}
.provider-panel {
  width: min(480px, 100%);
  padding: 32px;
  border: 1px solid var(--border-surface);
  border-radius: 8px;
  background: var(--gray-0);
}
h1 {
  font-size: 22px;
  color: var(--color-text);
  margin: 0 0 24px;
}
p {
  color: var(--color-text-secondary);
}
.ant-btn {
  margin-top: 16px;
}
</style>
