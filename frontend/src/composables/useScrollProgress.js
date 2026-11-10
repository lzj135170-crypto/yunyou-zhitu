import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useScrollProgress() {
  const progress = ref(0)
  let frame = null

  function update() {
    const doc = document.documentElement
    const max = Math.max(1, doc.scrollHeight - window.innerHeight)
    progress.value = Math.min(1, Math.max(0, window.scrollY / max))
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

  return { progress }
}
