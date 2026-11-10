import { onBeforeUnmount, onMounted, ref } from 'vue'

export function usePrefersReducedMotion() {
  const reduced = ref(false)
  let media = null

  function onChange(event) {
    reduced.value = event ? event.matches : Boolean(media && media.matches)
  }

  onMounted(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    media = window.matchMedia('(prefers-reduced-motion: reduce)')
    reduced.value = media.matches
    if (media.addEventListener) media.addEventListener('change', onChange)
    else if (media.addListener) media.addListener(onChange)
  })

  onBeforeUnmount(() => {
    if (!media) return
    if (media.removeEventListener) media.removeEventListener('change', onChange)
    else if (media.removeListener) media.removeListener(onChange)
    media = null
  })

  return reduced
}
