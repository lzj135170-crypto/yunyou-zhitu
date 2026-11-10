import { reactive, unref } from 'vue'
import { api } from '../api/client'

/**
 * 美食卡片的参考图。
 *
 * 美食推荐本身是菜品/小吃/美食街，不是具体餐厅；这里用高德搜到的高分餐厅
 * 照片作为“参考餐厅图”，并在卡片上明确标注来源。只按需加载可视卡片，
 * 避免一次结果页就打出 8 个高德请求；同一张卡片的结果在会话内复用。
 */
export function useFoodPhotos(token, { getActiveId }) {
  const photos = reactive({})
  const loading = reactive({})
  const failed = reactive({})
  let requestId = 0

  function keyOf(item) {
    return `${item?.name || ''}\u0000${item?.area || ''}`
  }

  function photoFor(item) {
    return photos[keyOf(item)] || ''
  }

  function isLoading(item) {
    return Boolean(loading[keyOf(item)])
  }

  function hasFailed(item) {
    return Boolean(failed[keyOf(item)])
  }

  async function load(item) {
    if (!item || !item.name) return
    const key = keyOf(item)
    if (photos[key] || loading[key] || failed[key]) return

    const id = getActiveId()
    if (!id) {
      failed[key] = true
      return
    }

    const current = requestId
    loading[key] = true
    try {
      const data = await api.getTripRestaurants(unref(token), id, {
        keyword: item.name,
        area: item.area || '',
        limit: 1,
      })
      if (current !== requestId) return
      const place = data && Array.isArray(data.places) ? data.places[0] : null
      if (place && place.photo_url) photos[key] = place.photo_url
      else failed[key] = true
    } catch {
      // 参考图只是增强；拿不到时卡片保留占位，不影响美食内容本身。
      if (current === requestId) failed[key] = true
    } finally {
      if (current === requestId) delete loading[key]
    }
  }

  function clear() {
    requestId += 1
    Object.keys(photos).forEach((key) => delete photos[key])
    Object.keys(loading).forEach((key) => delete loading[key])
    Object.keys(failed).forEach((key) => delete failed[key])
  }

  return {
    photos,
    load,
    photoFor,
    isLoading,
    hasFailed,
    clear,
  }
}
