import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 把一个元素的滚动经过比例映射到 0–1。
 * 用于行程织线：元素进入视口下沿时开始绘制，离开上沿前画满。
 */
export function useSectionProgress(target, { start = 0.82, end = 0.16 } = {}) {
  const progress = ref(0)
  let frame = null

  function update() {
    const element = target.value
    if (!element) {
      progress.value = 0
      return
    }
    const rect = element.getBoundingClientRect()
    const viewport = window.innerHeight
    const startY = viewport * start
    const endY = viewport * end
    const distance = Math.max(1, rect.height + startY - endY)
    const travelled = startY - rect.top
    progress.value = Math.min(1, Math.max(0, travelled / distance))
  }

  function schedule() {
    if (frame !== null) return
    frame = requestAnimationFrame(() => {
      frame = null
      update()
    })
  }

  onMounted(() => {
    update()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('scroll', schedule)
    window.removeEventListener('resize', schedule)
    if (frame !== null) {
      cancelAnimationFrame(frame)
      frame = null
    }
  })

  return progress
}
