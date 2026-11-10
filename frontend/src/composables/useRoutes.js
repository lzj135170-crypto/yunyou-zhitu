import { computed, ref, unref } from 'vue'
import { api } from '../api/client'
import { hotelMetaKey as makeHotelMetaKey, writeHotelMeta } from '../utils/storage'

export function useRoutes(token, {
  getPlan,
  getActiveId,
  getMapPoints,
  getUser,
  hotelMeta,
}) {
  const routePlan = ref(null)
  const routesLoading = ref(false)
  const routesError = ref('')
  const routeWarnings = ref([])
  const selectedRouteDay = ref(null)
  const hotelAddress = ref('')
  const selectedHotel = ref(null)
  const routesHotelAddress = ref('')
  let requestId = 0

  const routesStale = computed(() => {
    if (!routePlan.value) return false
    return hotelAddress.value.trim() !== (routesHotelAddress.value || '').trim()
  })

  const routeDaysToShow = computed(() => {
    const days = (routePlan.value && routePlan.value.days) || []
    if (!days.length) return []
    if (selectedRouteDay.value === null || selectedRouteDay.value === undefined) return days
    const day = days.find((item) => item.day === selectedRouteDay.value)
    return day ? [day] : days
  })

  function routablePlaces() {
    const plan = getPlan()
    const allowed = new Set(((plan && plan.attractions) || []).map((item) => item.name))
    return (getMapPoints() || [])
      .filter((point) => {
        const longitude = Number(point && point.longitude)
        const latitude = Number(point && point.latitude)
        return (
          point &&
          point.name &&
          allowed.has(point.name) &&
          Number.isFinite(longitude) &&
          Number.isFinite(latitude)
        )
      })
      .map((point) => ({
        name: point.name,
        longitude: Number(point.longitude),
        latitude: Number(point.latitude),
      }))
  }

  async function generateRoutes() {
    const activeId = getActiveId()
    const plan = getPlan()
    if (!activeId || !plan) {
      routesError.value = '还没有可规划的行程'
      return null
    }

    const places = routablePlaces()
    if (!places.length) {
      routesError.value = '还没有可用的景点坐标，请等地图加载完成'
      return null
    }

    const current = ++requestId
    const address = hotelAddress.value.trim()
    const payload = { places }

    if (selectedHotel.value) {
      payload.hotel_name = selectedHotel.value.name || ''
      const hasCoords =
        selectedHotel.value.longitude !== null &&
        selectedHotel.value.longitude !== undefined &&
        selectedHotel.value.latitude !== null &&
        selectedHotel.value.latitude !== undefined
      if (hasCoords) {
        payload.hotel_longitude = Number(selectedHotel.value.longitude)
        payload.hotel_latitude = Number(selectedHotel.value.latitude)
      }
    }
    if (address) payload.hotel_address = address

    routesLoading.value = true
    routesError.value = ''

    try {
      const data = await api.getTripRoutes(unref(token), activeId, payload)
      if (current !== requestId) return null

      routePlan.value = data || null
      routeWarnings.value = collectRouteWarnings(data)
      selectedRouteDay.value = defaultRouteDay(data)
      routesHotelAddress.value = address
      return routePlan.value
    } catch (e) {
      if (current === requestId) routesError.value = e.message
      return null
    } finally {
      if (current === requestId) routesLoading.value = false
    }
  }

  function clearRoutes() {
    requestId += 1
    routePlan.value = null
    routesLoading.value = false
    routesError.value = ''
    routeWarnings.value = []
    selectedRouteDay.value = null
  }

  function collectRouteWarnings(data) {
    return data && Array.isArray(data.warnings) ? data.warnings : []
  }

  function defaultRouteDay(data) {
    const days = (data && data.days) || []
    const withLegs = days.find((day) => (day.legs || []).length > 0)
    if (withLegs) return withLegs.day
    return days.length ? days[0].day : null
  }

  function selectHotel(place) {
    if (!place) return
    hotelAddress.value = place.address || place.name || ''
    selectedHotel.value = {
      name: place.name || '酒店',
      address: place.address || '',
      longitude: place.longitude,
      latitude: place.latitude,
      photo_url: place.photo_url || '',
      rating:
        place.rating === null || place.rating === undefined ? null : place.rating,
    }
    saveHotelMeta()
    generateRoutes()
  }

  function onHotelAddressInput() {
    // 手动改地址后，之前点选的坐标失效，必须让后端重新地理编码。
    selectedHotel.value = null
    saveHotelMeta()
  }

  function clearHotel() {
    resetHotelInputs()
    saveHotelMeta()
    clearRoutes()
  }

  function resetHotelInputs() {
    hotelAddress.value = ''
    selectedHotel.value = null
    routesHotelAddress.value = ''
  }

  function hotelMetaKey(conversationId) {
    return makeHotelMetaKey(getUser(), conversationId || getActiveId() || '')
  }

  function saveHotelMeta() {
    const activeId = getActiveId()
    if (!activeId || !getUser()) return
    const key = hotelMetaKey(activeId)
    const address = hotelAddress.value.trim()
    const name = selectedHotel.value ? selectedHotel.value.name || '' : ''
    const next = { ...hotelMeta.value }
    if (!address && !name) delete next[key]
    else next[key] = { address, name }
    hotelMeta.value = next
    writeHotelMeta(next)
  }

  function restoreHotelMeta(conversationId) {
    const meta = hotelMeta.value[hotelMetaKey(conversationId)] || {}
    hotelAddress.value = typeof meta.address === 'string' ? meta.address : ''
    const name = typeof meta.name === 'string' ? meta.name : ''
    selectedHotel.value = name
      ? { name, address: hotelAddress.value, longitude: null, latitude: null }
      : null
    routesHotelAddress.value = ''
  }

  function removeHotelMeta(conversationId) {
    const key = hotelMetaKey(conversationId)
    if (!(key in hotelMeta.value)) return
    const next = { ...hotelMeta.value }
    delete next[key]
    hotelMeta.value = next
    writeHotelMeta(next)
  }

  function selectRouteDay(day) {
    selectedRouteDay.value = day
  }

  return {
    routePlan,
    routesLoading,
    routesError,
    routeWarnings,
    selectedRouteDay,
    hotelAddress,
    selectedHotel,
    routesHotelAddress,
    routesStale,
    routeDaysToShow,
    routablePlaces,
    generateRoutes,
    clearRoutes,
    selectHotel,
    onHotelAddressInput,
    clearHotel,
    resetHotelInputs,
    restoreHotelMeta,
    removeHotelMeta,
    selectRouteDay,
  }
}
