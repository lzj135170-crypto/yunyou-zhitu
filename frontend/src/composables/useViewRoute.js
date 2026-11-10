import { onBeforeUnmount, onMounted, ref } from 'vue'

function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, '')
  if (raw.startsWith('result/')) {
    const id = Number(raw.slice('result/'.length))
    return { view: 'result', id: Number.isFinite(id) && id > 0 ? id : null }
  }
  if (raw === 'history') return { view: 'history', id: null }
  return { view: 'plan', id: null }
}

/**
 * 视图状态与 URL hash 同步。
 * #plan / #result/<id> / #history，刷新和浏览器后退都能恢复。
 */
export function useViewRoute({ onNavigate } = {}) {
  const view = ref('plan')
  const activeId = ref(null)

  function apply(state) {
    view.value = state.view
    activeId.value = state.id
    onNavigate?.(state)
  }

  function navigate(nextView, id = null, { replace = false } = {}) {
    const hash = nextView === 'result' && id ? `#result/${id}` : `#${nextView}`
    view.value = nextView
    activeId.value = id

    // 同一个 hash 不重复触发加载：调用方已经设置了 view/activeId。
    if (window.location.hash === hash) return

    if (replace) {
      window.history.replaceState(null, '', hash)
      onNavigate?.({ view: nextView, id })
    } else {
      window.location.hash = hash
    }
  }

  function handleHashChange() {
    apply(parseHash())
  }

  onMounted(() => {
    window.addEventListener('hashchange', handleHashChange)
    const state = parseHash()
    const raw = window.location.hash.replace(/^#\/?/, '')
    const known = raw === '' || raw === 'plan' || raw === 'history' || raw.startsWith('result/')
    if (!known || (state.view === 'result' && !state.id)) {
      window.history.replaceState(null, '', '#plan')
      apply({ view: 'plan', id: null })
      return
    }
    apply(state)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('hashchange', handleHashChange)
  })

  return { view, activeId, navigate, parseHash }
}
