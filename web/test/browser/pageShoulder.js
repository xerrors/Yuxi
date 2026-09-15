// Run from web/: pnpm exec vite --host 127.0.0.1; open /test/browser/pageShoulder.html.
// Real component and Ant Design controls; no API requests. Add ?dark for dark mode.
import { createApp, h, nextTick, ref } from 'vue'
import Antd, { theme, ConfigProvider, Select, Button } from 'ant-design-vue'
import PageShoulder from '../../src/components/shared/PageShoulder.vue'
import '../../src/assets/css/main.css'

const dark = new URLSearchParams(location.search).has('dark')
document.documentElement.classList.toggle('dark', dark)
const widths = [1024, 768, 375, 360]
const results = ref('Measuring…')
const search = ref('')
const clicks = ref(0)
createApp({
  render() {
    return h(
      ConfigProvider,
      { theme: { algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm } },
      () => [
        h('h1', 'PageShoulder layout regression'),
        h(
          'p',
          `Theme: ${dark ? 'dark' : 'light'}; Search: ${search.value}; Actions: ${clicks.value}`
        ),
        ...widths.map((width) =>
          h(
            'section',
            {
              style: {
                width: `${width}px`,
                '--page-padding': '24px',
                marginBottom: '24px',
                border: '1px solid var(--gray-200)'
              }
            },
            [
              h('h2', `${width}px container`),
              h(
                PageShoulder,
                {
                  search: search.value,
                  'onUpdate:search': (value) => {
                    search.value = value
                  },
                  searchPlaceholder: `Search ${width}`
                },
                {
                  filters: () =>
                    h(Select, {
                      style: { width: '120px' },
                      value: 'all',
                      options: [{ value: 'all', label: '全部分类' }]
                    }),
                  actions: () =>
                    ['刷新', '导入工具', '创建工具'].map((label) =>
                      h(
                        Button,
                        {
                          onClick: () => {
                            clicks.value++
                          }
                        },
                        () => label
                      )
                    )
                }
              )
            ]
          )
        ),
        h('pre', { id: 'results' }, results.value)
      ]
    )
  }
})
  .use(Antd)
  .mount('#app')
await nextTick()
await document.fonts.ready
requestAnimationFrame(() => {
  const measurements = [...document.querySelectorAll('section')].map((section) => {
    const shoulder = section.querySelector('.page-shoulder')
    const rect = shoulder.getBoundingClientRect()
    const controls = [...shoulder.querySelectorAll('input, button, .ant-select')]
    const overflowing = controls.filter((control) => {
      const bounds = control.getBoundingClientRect()
      return bounds.left < rect.left - 1 || bounds.right > rect.right + 1 || bounds.width < 20
    })
    return {
      width: Math.round(section.getBoundingClientRect().width),
      clientWidth: shoulder.clientWidth,
      scrollWidth: shoulder.scrollWidth,
      overflowingControls: overflowing.length,
      pass: shoulder.scrollWidth <= shoulder.clientWidth + 1 && overflowing.length === 0
    }
  })
  results.value = JSON.stringify(
    { pass: measurements.every((item) => item.pass), measurements },
    null,
    2
  )
})
