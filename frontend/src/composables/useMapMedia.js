import { onBeforeUnmount, ref, unref } from 'vue'
import { api } from '../api/client'

export function useMapMedia(token) {
  const mapUrl = ref('')
  const mapPoints = ref([])
  const mapLoading = ref(false)
  const mapError = ref('')
  const photoMap = ref({})
  let requestId = 0

  function releaseMapUrl() {
    if (mapUrl.value && mapUrl.value.startsWith('blob:')) {
      URL.revokeObjectURL(mapUrl.value)
    }
    mapUrl.value = ''
  }

  function clear() {
    requestId += 1
    releaseMapUrl()
    mapPoints.value = []
    photoMap.value = {}
    mapError.value = ''
    mapLoading.value = false
  }

  async function load(conversationId) {
    clear()
    const current = ++requestId
    mapLoading.value = true

    try {
      const media = await api.getTripMedia(unref(token), conversationId)
      if (current !== requestId) return

      if (media && media.map_png) {
        mapUrl.value = `data:image/png;base64,${media.map_png}`
      }
      mapPoints.value = media && Array.isArray(media.places) ? media.places : []

      const photos = {}
      const items = media && Array.isArray(media.photos) ? media.photos : []
      items.forEach((photo) => {
        if (photo && photo.name && photo.url) photos[photo.name] = photo.url
      })
      photoMap.value = photos
    } catch (e) {
      if (current === requestId) mapError.value = e.message
    } finally {
      if (current === requestId) mapLoading.value = false
    }
  }

  function photoFor(name) {
    return (photoMap.value && photoMap.value[name]) || ''
  }

  function dropPhoto(name) {
    if (!photoMap.value || !photoMap.value[name]) return
    const next = { ...photoMap.value }
    delete next[name]
    photoMap.value = next
  }

  onBeforeUnmount(releaseMapUrl)

  return {
    mapUrl,
    mapPoints,
    mapLoading,
    mapError,
    photoMap,
    load,
    clear,
    releaseMapUrl,
    photoFor,
    dropPhoto,
  }
}
