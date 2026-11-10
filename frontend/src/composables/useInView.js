import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useInView(target, { threshold = 0.2, rootMargin = '0px', once = false } = {}) {
  const inView = ref(false)
  let observer = null

  onMounted(() => {
    const element = target.value
    if (!element) return
    if (typeof IntersectionObserver === 'undefined') {
      inView.value = true
      return
    }
    observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            inView.value = true
            if (once) observer?.disconnect()
          } else if (!once) {
            inView.value = false
          }
        })
      },
      { threshold, rootMargin }
    )
    observer.observe(element)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })

  return inView
}
