import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * 结果页目录高亮。
 *
 * 用滚动位置计算“最后一个已越过顶部偏移的 section”，比
 * IntersectionObserver 的 rootMargin 更容易和吸顶 Header 对齐。
 */
export function useScrollSpy(sectionIds) {
  const activeSection = ref('')
  let frame = 0

  function setActive(id) {
    if (id) activeSection.value = id
  }

  function update() {
    const list = sectionIds.value || []
    if (!list.length) return

    // 页面到底时，最后一个 section 可能永远到不了顶部偏移，直接选中它。
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
      activeSection.value = list[list.length - 1]
      return
    }

    let current = list[0]
    const header = document.querySelector('.app-header')
    const offset = (header ? header.offsetHeight : 72) + 40
    list.forEach((id) => {
      const element = document.getElementById(id)
      if (element && element.getBoundingClientRect().top <= offset) current = id
    })
    activeSection.value = current
  }

  function onScroll() {
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(update)
  }

  onMounted(async () => {
    await nextTick()
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
  })

  onBeforeUnmount(() => {
    cancelAnimationFrame(frame)
    window.removeEventListener('scroll', onScroll)
    window.removeEventListener('resize', onScroll)
  })

  watch(sectionIds, () => nextTick(update), { deep: true })

  return { activeSection, setActive }
}
