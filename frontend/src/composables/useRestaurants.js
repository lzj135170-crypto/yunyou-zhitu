import { ref, unref } from 'vue'
import { api } from '../api/client'
import { useImageFailures } from './useImageFailures'

export function useRestaurants(token, { getActiveId }) {
  const target = ref(null)
  const places = ref([])
  const loading = ref(false)
  const error = ref('')
  const images = useImageFailures()
  let requestId = 0

  function isTarget(item) {
    return !!(target.value && item && target.value.name === item.name)
  }

  function buttonLabel(item) {
    if (isTarget(item) && loading.value) return '查询中…'
    if (isTarget(item)) return '收起餐厅'
    return '看附近餐厅'
  }

  async function load(item) {
    const current = ++requestId
    target.value = { name: item.name, area: item.area || '' }
    places.value = []
    error.value = ''
    loading.value = true
    images.clearFailures()

    try {
      const data = await api.getTripRestaurants(unref(token), getActiveId(), {
        keyword: item.name,
        area: item.area || '',
      })
      if (current !== requestId) return
      places.value = data && Array.isArray(data.places) ? data.places : []
      if (!places.value.length && data && Array.isArray(data.warnings)) {
        error.value = data.warnings.join('；')
      }
    } catch (e) {
      if (current === requestId) error.value = e.message
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  function toggle(item) {
    if (isTarget(item)) {
      clear()
      return false
    }
    load(item)
    return true
  }

  function clear() {
    requestId += 1
    target.value = null
    places.value = []
    loading.value = false
    error.value = ''
    images.clearFailures()
  }

  function onPhotoError(url) {
    images.markFailed(url)
  }

  function photoFailed(url) {
    return images.hasFailed(url)
  }

  return {
    target,
    places,
    loading,
    error,
    load,
    toggle,
    clear,
    isTarget,
    buttonLabel,
    onPhotoError,
    photoFailed,
  }
}
