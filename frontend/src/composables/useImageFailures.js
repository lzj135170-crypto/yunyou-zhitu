import { ref } from 'vue'

export function useImageFailures() {
  const failed = ref(new Set())

  function hasFailed(url) {
    return Boolean(url) && failed.value.has(url)
  }

  function markFailed(url) {
    if (!url || failed.value.has(url)) return
    const next = new Set(failed.value)
    next.add(url)
    failed.value = next
  }

  function clearFailures() {
    failed.value = new Set()
  }

  return { failed, hasFailed, markFailed, clearFailures }
}
