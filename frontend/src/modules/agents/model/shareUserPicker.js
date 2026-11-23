import { computed, onScopeDispose, ref, watch } from 'vue'

const SEARCH_DEBOUNCE_MS = 300
const MANAGE_USER_ROLES = ['admin', 'superadmin']

const toOption = (user) => ({
  label: user.department_name ? `${user.username}（${user.department_name}）` : user.username,
  value: user.uid,
  role: user.role
})

/** 分开读取可添加候选与既有授权，且只发布当前弹层请求的结果。 */
export const useShareUserPicker = ({ getOptions, selectedUids, canSearch, manage }) => {
  const open = ref(false)
  const query = ref('')
  const status = ref('idle')
  const matches = ref([])
  const selectedLabels = ref(new Map())
  const selected = computed(() =>
    selectedUids().map((uid) => {
      const option = selectedLabels.value.get(uid)
      if (!option) return { label: uid, value: uid }
      if (manage && !MANAGE_USER_ROLES.includes(option.role)) {
        return { ...option, label: `${option.label}（已非管理员）` }
      }
      return option
    })
  )
  const candidates = computed(() => {
    const selectedIds = new Set(selectedUids())
    return matches.value.filter((option) => {
      if (selectedIds.has(option.value)) return false
      const known = selectedLabels.value.has(option.value)
        ? selectedLabels.value.get(option.value)
        : option
      return known && (!manage || MANAGE_USER_ROLES.includes(known.role))
    })
  })

  const active = computed(() => open.value && canSearch())
  watch(
    [active, () => query.value.trim()],
    ([active, search], _, onCleanup) => {
      matches.value = []
      status.value = active ? 'loading' : 'idle'
      if (!active) return

      let current = true
      const load = async () => {
        try {
          // 尚未解析就移除的授权，只能经下一次新候选查询重新出现。
          const selectedIds = new Set(selectedUids())
          const stillRelevant = [...selectedLabels.value].filter(
            ([uid, option]) => option !== undefined || selectedIds.has(uid)
          )
          if (stillRelevant.length !== selectedLabels.value.size) {
            selectedLabels.value = new Map(stillRelevant)
          }
          const rows = await getOptions({
            search,
            ...(manage ? { roles: MANAGE_USER_ROLES } : {}),
            limit: 50
          })
          if (!current) return
          matches.value = rows.map(toOption)
          status.value = 'ready'
        } catch {
          if (current) status.value = 'error'
        }
      }

      // 输入改变时立即收回结果发布权，不等下一次防抖请求发出。
      const timer = search ? setTimeout(load, SEARCH_DEBOUNCE_MS) : null
      onCleanup(() => {
        current = false
        if (timer !== null) clearTimeout(timer)
      })
      if (timer === null) load()
    },
    { flush: 'sync' }
  )

  watch(
    [active, () => JSON.stringify(selectedUids())],
    async ([active], _, onCleanup) => {
      if (!active) {
        selectedLabels.value = new Map()
        return
      }

      // 点选已有候选时保留现成标签；编辑既有授权时，仅查询尚未查过的 UID。
      const labels = new Map(selectedLabels.value)
      const includeUids = []
      for (const uid of selectedUids()) {
        if (labels.has(uid) && labels.get(uid) !== undefined) continue
        const match = !labels.has(uid) && matches.value.find((option) => option.value === uid)
        if (match) {
          labels.set(uid, match)
        } else {
          labels.set(uid, undefined)
          includeUids.push(uid)
        }
      }
      selectedLabels.value = labels
      if (!includeUids.length) return

      let current = true
      onCleanup(() => {
        current = false
      })
      try {
        const rows = await getOptions({ includeUids, limit: 0 })
        if (!current) return
        const resolved = new Map(selectedLabels.value)
        for (const uid of includeUids) resolved.set(uid, null)
        for (const row of rows) resolved.set(row.uid, toOption(row))
        selectedLabels.value = resolved
      } catch {
        // 标签不可用时仍按原 UID 显示，不能让既有授权无法移除。
      }
    },
    { flush: 'sync' }
  )

  const setOpen = (value) => {
    open.value = value
    if (!value) query.value = ''
  }
  onScopeDispose(() => setOpen(false))

  return { open, query, selected, candidates, status, setOpen }
}
