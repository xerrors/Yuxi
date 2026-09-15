import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import Antd, { ConfigProvider, theme } from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import '@/assets/css/main.css'
import DocumentLimitsSettings from '@/components/DocumentLimitsSettings.vue'
import { useUserStore } from '@/stores/user'
const params = new URLSearchParams(location.search)
const dark = params.has('dark')
document.documentElement.classList.toggle('dark', dark)
let conflict = params.has('conflict')
let fail = params.has('error')
let current = {
  upload_max_mib: 100,
  ocr_max_pages: 500,
  hard_upload_max_mib: 300,
  hard_ocr_max_pages: 800,
  effective_upload_max_bytes: 104857600,
  revision: 0,
  defaults: { upload_max_mib: 100, ocr_max_pages: 500 }
}
window.fetch = async (url, options = {}) => {
  if (!String(url).endsWith('/api/system/document-limits'))
    throw new Error('Fixture blocks external requests')
  const method = options.method || 'GET'
  if (fail) {
    fail = false
    return new Response(JSON.stringify({ detail: 'fixture unavailable' }), { status: 503 })
  }
  if (method !== 'GET') {
    if (conflict) {
      conflict = false
      return new Response(JSON.stringify({ detail: 'fixture conflict' }), { status: 409 })
    }
    const body = JSON.parse(options.body)
    current = {
      ...current,
      ...(method === 'DELETE' ? current.defaults : body),
      revision: current.revision + 1
    }
    current.effective_upload_max_bytes = current.upload_max_mib * 1024 * 1024
  }
  return new Response(JSON.stringify(current), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}
const pinia = createPinia()
setActivePinia(pinia)
const user = useUserStore()
user.token = 'fixture'
user.userRole = 'superadmin'
createApp({
  render: () =>
    h(
      ConfigProvider,
      { theme: { algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm } },
      () =>
        h(
          'main',
          {
            style: {
              maxWidth: params.has('narrow') ? '340px' : '820px',
              margin: '20px auto',
              padding: '16px',
              background: dark ? '#141414' : 'white',
              color: dark ? 'white' : '#222'
            }
          },
          [h('p', '合成设置验收，不连接生产服务'), h(DocumentLimitsSettings)]
        )
    )
})
  .use(pinia)
  .use(Antd)
  .mount('#app')
