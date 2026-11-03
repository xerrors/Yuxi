const ICON_BASE = 'https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons'
const WHITE_ICON_FILTER = 'brightness(0) invert(1)'

const avatar = (icon, background, scale = 0.75, filter = WHITE_ICON_FILTER) => ({
  icon: `${ICON_BASE}/${icon}.svg`,
  background,
  scale,
  filter
})

export const modelAvatars = {
  default: avatar('default', 'var(--gray-100)', 0.72, 'none'),
  alibaba: avatar('alibaba', '#ff6003', 0.8),
  'alibaba-cn': avatar('bailian-color', '#fff', 0.75, 'none'),
  'alibaba-coding-plan': avatar('alibabacloud', '#ff6a00', 0.7),
  'alibaba-coding-plan-cn': avatar('alibabacloud', '#ff6a00', 0.7),
  anthropic: avatar('anthropic', '#f1f0e8', 0.75, 'none'),
  ark: avatar('volcengine-color', '#fff', 0.75, 'none'),
  dashscope: avatar('bailian-color', '#fff', 0.75, 'none'),
  deepseek: avatar('deepseek', '#4d6bfe'),
  google: avatar('google-color', '#fff', 0.75, 'none'),
  minimax: avatar('minimax', 'linear-gradient(to right, #e2167e, #fe603c)'),
  'minimax-cn': avatar('minimax', 'linear-gradient(to right, #e2167e, #fe603c)'),
  modelscope: avatar('modelscope', '#624aff'),
  moonshotai: avatar('moonshot', '#16191e'),
  'moonshotai-cn': avatar('moonshot', '#16191e'),
  opencode: avatar('opencode', '#000'),
  'opencode-go': avatar('opencode', '#000'),
  openai: avatar('openai', '#000'),
  openrouter: avatar(
    'openrouter',
    '#000',
    0.75,
    'brightness(0) saturate(100%) invert(94%) sepia(94%) saturate(1636%) hue-rotate(24deg) brightness(105%) contrast(106%)'
  ),
  siliconflow: avatar('siliconcloud', '#6e29f6', 0.7),
  'siliconflow-cn': avatar('siliconcloud', '#6e29f6', 0.7),
  together: avatar('together', '#fff', 0.75, 'none'),
  'kimi-for-coding': avatar('moonshot', '#16191e'),
  xiaomi: avatar('xiaomimimo', '#000', 0.7),
  'xiaomi-token-plan-cn': avatar('xiaomimimo', '#000', 0.7),
  zai: avatar('zai', '#000', 0.6),
  'zai-coding-plan': avatar('zai', '#000', 0.6),
  zhipu: avatar('zhipu', '#3859ff'),
  zhipuai: avatar('zhipu', '#3859ff'),
  'zhipuai-coding-plan': avatar('zhipu', '#3859ff')
}
