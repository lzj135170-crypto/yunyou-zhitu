function motionBehavior(instant) {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return instant || reduce ? 'auto' : 'smooth'
}

export function scrollToId(id, options = {}) {
  const element = document.getElementById(id)
  if (!element) return
  element.scrollIntoView({
    behavior: motionBehavior(options.instant),
    block: 'start',
  })
}

export function scrollToTop(instant = false) {
  window.scrollTo({
    top: 0,
    behavior: motionBehavior(instant),
  })
}
