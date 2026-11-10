import { onBeforeUnmount, ref, toValue, watch } from 'vue'

export function useCountUp(source, { active = ref(true), duration = 720 } = {}) {
  const display = ref(0)
  let frame = null
  let startedAt = 0
  let from = 0
  let target = 0
  let hasPlayed = false

  function stop() {
    if (frame !== null) {
      cancelAnimationFrame(frame)
      frame = null
    }
  }

  function tick(now) {
    const progress = Math.min(1, (now - startedAt) / duration)
    const eased = 1 - Math.pow(1 - progress, 3)
    display.value = from + (target - from) * eased
    if (progress < 1) {
      frame = requestAnimationFrame(tick)
    } else {
      display.value = target
      frame = null
    }
  }

  function start() {
    stop()
    const value = Number(toValue(source)) || 0
    target = value
    if (!active.value) {
      if (!hasPlayed) display.value = 0
      return
    }
    if (duration <= 0) {
      display.value = value
      return
    }
    from = hasPlayed ? Number(display.value) || 0 : 0
    hasPlayed = true
    startedAt = performance.now()
    frame = requestAnimationFrame(tick)
  }

  watch([() => toValue(source), () => active.value], start, { immediate: true })

  onBeforeUnmount(stop)

  return { display }
}
