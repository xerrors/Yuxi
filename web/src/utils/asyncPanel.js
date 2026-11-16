import { defineAsyncComponent, h } from 'vue'
import { Button, Spin } from 'ant-design-vue'

const PanelStatus = ({ error }) =>
  error
    ? h('div', { role: 'alert' }, [
        h('p', '面板加载失败，请检查网络后重新加载页面。'),
        h(Button, { onClick: () => window.location.reload() }, () => '重新加载页面')
      ])
    : h('div', { role: 'status', 'aria-label': '正在加载面板' }, [h(Spin)])

PanelStatus.props = ['error']

/** 为按需面板提供一致的加载与失败提示。 */
export const createAsyncPanel = (loader) =>
  defineAsyncComponent({
    loader,
    loadingComponent: PanelStatus,
    errorComponent: PanelStatus,
    delay: 150,
    timeout: 30000
  })
