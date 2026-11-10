import { ref, unref } from 'vue'
import { api } from '../api/client'
import { useImageFailures } from './useImageFailures'

export function useHotels(token, { getPlan }) {
  const areas = ref([])
  const loading = ref(false)
  const error = ref('')
  const tierChoices = ref({})
  const images = useImageFailures()
  let requestId = 0

  function clear() {
    requestId += 1
    areas.value = []
    loading.value = false
    error.value = ''
    images.clearFailures()
    tierChoices.value = {}
  }

  async function load(conversationId) {
    clear()
    const plan = getPlan()
    if (!plan || !Array.isArray(plan.hotels) || !plan.hotels.length) return

    const current = ++requestId
    loading.value = true

    try {
      const data = await api.getTripHotels(unref(token), conversationId)
      if (current !== requestId) return
      areas.value = data && Array.isArray(data.areas) ? data.areas : []
    } catch (e) {
      if (current === requestId) error.value = e.message
    } finally {
      if (current === requestId) loading.value = false
    }
  }

  function areaFor(index) {
    return (areas.value || []).find((item) => item.index === index) || null
  }

  function tiersFor(index) {
    const area = areaFor(index)
    if (area && Array.isArray(area.tiers) && area.tiers.length) return area.tiers
    if (area && Array.isArray(area.places) && area.places.length) {
      return [{ key: 'all', label: '酒店', places: area.places, error: area.error || '' }]
    }
    return []
  }

  function defaultTier(index) {
    const plan = getPlan()
    const planHotel = plan && plan.hotels ? plan.hotels[index] : null
    const level = (planHotel && planHotel.level) || ''
    if (/经济型|民宿|公寓|青旅|青年旅舍|客栈/.test(level)) return 'budget'
    if (/舒适|高档|中档|商务|精品|星级/.test(level)) return 'comfort'
    const tripLevel = (plan && plan.hotel_level) || ''
    return /经济型|民宿/.test(tripLevel) ? 'budget' : 'comfort'
  }

  function tierKey(index) {
    const tiers = tiersFor(index)
    if (!tiers.length) return ''

    const chosen = tierChoices.value[index]
    if (chosen && tiers.some((tier) => tier.key === chosen)) return chosen

    const fallback = defaultTier(index)
    if (tiers.some((tier) => tier.key === fallback)) {
      const hasAny = tiers.some((tier) => tier.places.length)
      const fallbackTier = tiers.find((tier) => tier.key === fallback)
      if (fallbackTier.places.length || !hasAny) return fallback
    }
    const withPlaces = tiers.find((tier) => tier.places.length)
    return (withPlaces || tiers[0]).key
  }

  function tierFor(index) {
    const key = tierKey(index)
    return tiersFor(index).find((tier) => tier.key === key) || null
  }

  function selectTier(index, key) {
    tierChoices.value = { ...tierChoices.value, [index]: key }
  }

  function placesFor(index) {
    const tier = tierFor(index)
    return tier && Array.isArray(tier.places) ? tier.places : []
  }

  function tierError(index) {
    const tier = tierFor(index)
    if (tier && tier.error) return tier.error
    const area = areaFor(index)
    return area && area.error ? area.error : ''
  }

  function onPhotoError(url) {
    images.markFailed(url)
  }

  function photoFailed(url) {
    return images.hasFailed(url)
  }

  return {
    areas,
    loading,
    error,
    tierChoices,
    load,
    clear,
    areaFor,
    tiersFor,
    defaultTier,
    tierKey,
    tierFor,
    selectTier,
    placesFor,
    tierError,
    onPhotoError,
    photoFailed,
  }
}
