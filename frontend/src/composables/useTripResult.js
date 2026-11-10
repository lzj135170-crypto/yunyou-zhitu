import { computed, nextTick, ref, unref } from 'vue'
import { api } from '../api/client'
import { downloadMarkdown as saveMarkdown } from '../utils/exportMarkdown'
import { dayDate, parseDateOnly } from '../utils/date'
import { routeModeColor, routeModeLabel } from '../utils/routeModes'
import { readHotelMeta, readTripMeta, writeTripMeta } from '../utils/storage'
import { useFoodPhotos } from './useFoodPhotos'
import { useHotels } from './useHotels'
import { useMapMedia } from './useMapMedia'
import { useRestaurants } from './useRestaurants'
import { useRoutes } from './useRoutes'

export function useTripResult({ token, getUser, activeId, navigate, showToast }) {
  const activeConversation = ref(null)
  const plan = ref(null)
  const planLoading = ref(false)
  const planError = ref('')
  const creatingTrip = ref(false)
  const adjustText = ref('')
  const adjustLoading = ref(false)
  const showAnswer = ref(false)
  const loadingSeconds = ref(0)
  const tripMeta = ref(readTripMeta())
  const hotelMeta = ref(readHotelMeta())
  const conversations = ref([])
  const historyLoading = ref(false)
  let loadingTimer = null
  let planRequestId = 0
  let openingId = null

  const media = useMapMedia(token)
  const hotels = useHotels(token, { getPlan: () => plan.value })
  const restaurants = useRestaurants(token, { getActiveId: () => activeId.value })
  const foodPhotos = useFoodPhotos(token, { getActiveId: () => activeId.value })
  const routes = useRoutes(token, {
    getPlan: () => plan.value,
    getActiveId: () => activeId.value,
    getMapPoints: () => media.mapPoints.value,
    getUser,
    hotelMeta,
  })

  const activeMeta = computed(() => {
    if (!activeId.value) return {}
    const local = tripMeta.value[activeId.value] || {}
    const value = plan.value || {}
    const merged = { ...local }

    const fieldMap = {
      start_date: 'startDate',
      end_date: 'endDate',
      travelers: 'travelers',
      budget: 'budget',
      pace: 'pace',
      hotel_level: 'hotelLevel',
    }
    Object.entries(fieldMap).forEach(([planKey, localKey]) => {
      const field = value[planKey]
      if (field !== null && field !== undefined && field !== '') {
        merged[localKey] = field
      }
    })

    if (value.destination) merged.destination = value.destination
    return merged
  })

  const tripDayCount = computed(() => {
    const meta = activeMeta.value
    if (meta.startDate && meta.endDate) {
      const start = parseDateOnly(meta.startDate)
      const end = parseDateOnly(meta.endDate)
      if (start && end) {
        const diff = Math.round((end.getTime() - start.getTime()) / 86400000)
        if (diff >= 0) return diff + 1
      }
    }
    if (plan.value && plan.value.days && plan.value.days.length) return plan.value.days.length
    return 0
  })

  const tripDateRange = computed(() => {
    const meta = activeMeta.value
    if (meta.startDate && meta.endDate) return `${meta.startDate} 至 ${meta.endDate}`
    const weatherDays = plan.value && plan.value.weather ? plan.value.weather.days : []
    if (weatherDays && weatherDays.length) {
      return `${weatherDays[0].date} 至 ${weatherDays[weatherDays.length - 1].date}`
    }
    if (plan.value && plan.value.days && plan.value.days.length) {
      return `${plan.value.days.length} 天行程`
    }
    return 'AI 生成行程'
  })

  const hasBudget = computed(() => {
    const budget = Number(activeMeta.value.budget)
    return Number.isFinite(budget) && budget > 0
  })

  const budgetDisplay = computed(() => {
    if (!hasBudget.value) return '—'
    return Number(activeMeta.value.budget).toLocaleString('zh-CN')
  })

  const budgetCaption = computed(() => {
    const budget = Number(activeMeta.value.budget)
    if (!Number.isFinite(budget) || budget <= 0) {
      return '还没有设置预算目标，可以在规划页补充。'
    }
    return `目标预算 · ${activeMeta.value.travelers || '—'} 人 / ${tripDayCount.value} 天`
  })

  const perPersonBudget = computed(() => {
    const budget = Number(activeMeta.value.budget)
    const travelers = Number(activeMeta.value.travelers)
    if (!Number.isFinite(budget) || budget <= 0 || !Number.isFinite(travelers) || travelers <= 0) {
      return '—'
    }
    return `¥${Math.round(budget / travelers).toLocaleString('zh-CN')}`
  })

  const hasPhotos = computed(() => Object.keys(media.photoMap.value || {}).length > 0)

  const hasFood = computed(() => {
    if (!plan.value) return false
    return (
      (Array.isArray(plan.value.foods) && plan.value.foods.length > 0) ||
      !!plan.value.food_note
    )
  })

  const visibleMapPoints = computed(() => {
    const points = [...(media.mapPoints.value || [])]
    if (!routes.routePlan.value) return points
    if (routes.selectedRouteDay.value !== null && routes.selectedRouteDay.value !== undefined) {
      const day = routes.routeDaysToShow.value[0]
      if (day) {
        const names = new Set(day.places || [])
        return points.filter((point) => names.has(point.name))
      }
    }
    return points
  })

  const visibleHotelPoints = computed(() => {
    const hotel = routes.routePlan.value && routes.routePlan.value.hotel
    if (!hotel) return []
    const selected = routes.selectedHotel.value || {}
    return [
      {
        name: hotel.name || selected.name || '酒店',
        address: hotel.address || selected.address || '',
        longitude: hotel.longitude,
        latitude: hotel.latitude,
        photo_url: selected.photo_url || hotel.photo_url || '',
        rating:
          selected.rating === undefined || selected.rating === null ? null : selected.rating,
      },
    ]
  })

  const visibleRouteLines = computed(() => {
    if (!routes.routePlan.value) return []
    return routes.routeDaysToShow.value.flatMap((day) =>
      (day.legs || []).map((leg) => ({
        mode: leg.recommended.mode,
        polyline: leg.recommended.polyline,
        estimated: leg.recommended.estimated,
      }))
    )
  })

  const mapLegendAttractions = computed(() => {
    if (routes.routePlan.value) return visibleMapPoints.value
    return (plan.value && plan.value.attractions) || []
  })

  const mapLegendItems = computed(() => {
    const seen = new Set()
    const items = []
    visibleRouteLines.value.forEach((line) => {
      if (!line.mode || seen.has(line.mode)) return
      seen.add(line.mode)
      items.push({
        mode: line.mode,
        label: routeModeLabel(line.mode),
        color: routeModeColor(line.mode),
      })
    })
    return items
  })

  const resultSections = computed(() => {
    const sections = [
      { id: 'overview', label: '行程概览', icon: 'compass' },
      { id: 'map', label: '景点路线', icon: 'map' },
      { id: 'weather', label: '天气信息', icon: 'sun' },
      { id: 'itinerary', label: '每日行程', icon: 'calendar' },
      { id: 'attractions', label: '景点推荐', icon: 'star' },
      { id: 'hotels', label: '住宿建议', icon: 'hotel' },
      { id: 'routes', label: '按天路线', icon: 'route' },
      { id: 'transport', label: '交通方案', icon: 'train' },
      { id: 'budget', label: '预算速览', icon: 'wallet' },
      { id: 'tips', label: '旅行提示', icon: 'sparkle' },
      { id: 'sources', label: '参考资料', icon: 'book' },
      { id: 'answer', label: '完整回复', icon: 'list' },
      { id: 'adjust', label: '智能调整', icon: 'pencil' },
    ]
    if (!hasFood.value) return sections
    const index = sections.findIndex((item) => item.id === 'attractions')
    sections.splice(index + 1, 0, { id: 'food', label: '美食推荐', icon: 'utensils' })
    return sections
  })

  function dayLabel(dayNumber) {
    return dayDate(dayNumber, activeMeta.value, plan.value)
  }

  function startLoadingTimer() {
    stopLoadingTimer()
    loadingSeconds.value = 0
    loadingTimer = setInterval(() => {
      loadingSeconds.value += 1
    }, 1000)
  }

  function stopLoadingTimer() {
    if (loadingTimer) {
      clearInterval(loadingTimer)
      loadingTimer = null
    }
  }

  function handleError(e) {
    if (e && e.code === 401) return
    showToast(e.message || '请求失败，请稍后重试', 'error')
  }

  function makeFallbackPlan(conversationId, answer, meta) {
    const text = (answer || '').trim()
    return {
      conversation_id: conversationId,
      title: activeConversation.value ? activeConversation.value.title : '',
      destination: meta ? meta.destination : '',
      overview: text ? `${text.slice(0, 220)}${text.length > 220 ? '…' : ''}` : '已生成回复。',
      answer: text,
      days: [],
      attractions: [],
      foods: [],
      food_note: '',
      hotels: [],
      transport: [],
      weather: null,
      budget_assumption: '',
      tips: [],
      warnings: [],
      sources: [],
      generated_at: null,
    }
  }

  async function loadPlan(conversationId, options = {}) {
    const current = ++planRequestId
    planLoading.value = true
    planError.value = ''
    showAnswer.value = false
    routes.clearRoutes()
    restaurants.clear()
    foodPhotos.clear()
    routes.restoreHotelMeta(conversationId)

    try {
      const data = await api.getLatestPlan(unref(token), conversationId)
      if (current !== planRequestId) return
      plan.value = data
      media.load(conversationId)
      hotels.load(conversationId)
    } catch (e) {
      if (current !== planRequestId) return
      if (e.code === 404 && options.fallbackAnswer) {
        plan.value = makeFallbackPlan(
          conversationId,
          options.fallbackAnswer,
          options.fallbackMeta
        )
        media.clear()
        hotels.clear()
      } else {
        plan.value = null
        planError.value = e.message
        media.clear()
        hotels.clear()
        if (e.code !== 404) handleError(e)
      }
    } finally {
      if (current === planRequestId) planLoading.value = false
    }
  }

  function saveTripMeta(conversationId, meta) {
    tripMeta.value = { ...tripMeta.value, [conversationId]: meta }
    writeTripMeta(tripMeta.value)
  }

  function removeTripMeta(conversationId) {
    const next = { ...tripMeta.value }
    delete next[conversationId]
    tripMeta.value = next
    writeTripMeta(next)
  }

  function metaFor(conv) {
    return tripMeta.value[conv.id] || {}
  }

  function historySubtitle(conv) {
    const meta = metaFor(conv)
    const parts = []
    if (meta.startDate && meta.endDate) parts.push(`${meta.startDate} 至 ${meta.endDate}`)
    if (meta.travelers) parts.push(`${meta.travelers} 人`)
    if (Number(meta.budget) > 0) {
      parts.push(`预算 ¥${Number(meta.budget).toLocaleString('zh-CN')}`)
    }
    return parts.length ? parts.join(' · ') : '点击查看这次对话生成的行程。'
  }

  function resetTripState() {
    planRequestId += 1
    activeConversation.value = null
    activeId.value = null
    plan.value = null
    planError.value = ''
    showAnswer.value = false
    adjustText.value = ''
    media.clear()
    hotels.clear()
    restaurants.clear()
    foodPhotos.clear()
    routes.clearRoutes()
    routes.resetHotelInputs()
  }

  function newTrip() {
    resetTripState()
    navigate('plan')
  }

  async function openConversation(conv) {
    activeConversation.value = conv
    activeId.value = conv.id
    plan.value = null
    planError.value = ''
    showAnswer.value = false
    adjustText.value = ''
    media.clear()
    hotels.clear()
    navigate('result', conv.id)
    await loadPlan(conv.id)
  }

  async function openConversationById(id) {
    if (!id) return
    if (openingId === id) return
    if (
      activeId.value === id &&
      (plan.value || planLoading.value || creatingTrip.value)
    ) {
      return
    }
    openingId = id
    try {
      const conv = await api.getConversation(unref(token), id)
      await openConversation(conv)
    } catch (e) {
      handleError(e)
      navigate('plan')
    } finally {
      openingId = null
    }
  }

  async function createTrip(form, prompt) {
    if (creatingTrip.value) return
    creatingTrip.value = true
    startLoadingTimer()
    planError.value = ''
    plan.value = null
    media.clear()

    try {
      const title = `${form.destination} · ${form.days} 天`
      const conversation = await api.createConversation(unref(token), title)
      saveTripMeta(conversation.id, form)

      const data = await api.sendMessage(unref(token), conversation.id, prompt)
      activeConversation.value = conversation
      activeId.value = conversation.id
      await loadPlan(conversation.id, {
        fallbackAnswer: data.assistant_message.content,
        fallbackMeta: form,
      })
      navigate('result', conversation.id)
    } catch (e) {
      handleError(e)
    } finally {
      creatingTrip.value = false
      stopLoadingTimer()
    }
  }

  async function sendAdjustment() {
    if (adjustLoading.value) return
    if (!activeId.value) {
      showToast('当前没有可调整的行程', 'info')
      return
    }
    const content = adjustText.value.trim()
    if (!content) return

    adjustLoading.value = true
    startLoadingTimer()

    try {
      const data = await api.sendMessage(unref(token), activeId.value, content)
      adjustText.value = ''
      await loadPlan(activeId.value, {
        fallbackAnswer: data.assistant_message.content,
        fallbackMeta: activeMeta.value,
      })
      showToast('行程已更新', 'success')
    } catch (e) {
      handleError(e)
    } finally {
      adjustLoading.value = false
      stopLoadingTimer()
    }
  }

  async function loadConversations() {
    historyLoading.value = true
    try {
      conversations.value = await api.listConversations(unref(token))
    } catch (e) {
      handleError(e)
    } finally {
      historyLoading.value = false
    }
  }

  async function deleteConversation(conv) {
    try {
      await api.deleteConversation(unref(token), conv.id)
      conversations.value = conversations.value.filter((item) => item.id !== conv.id)
      removeTripMeta(conv.id)
      routes.removeHotelMeta(conv.id)
      if (activeId.value === conv.id) {
        resetTripState()
      }
      return true
    } catch (e) {
      handleError(e)
      return false
    }
  }

  function downloadMarkdown() {
    if (!plan.value) {
      showToast('还没有可导出的行程', 'info')
      return
    }
    saveMarkdown(plan.value, activeMeta.value, tripDateRange.value)
  }

  function printPlan() {
    if (!plan.value) {
      showToast('还没有可导出的行程', 'info')
      return
    }
    // 打印主题会强制展开完整回复，不改变用户屏幕上的折叠状态。
    nextTick(() => window.print())
  }

  return {
    activeId,
    activeConversation,
    plan,
    planLoading,
    planError,
    creatingTrip,
    adjustText,
    adjustLoading,
    showAnswer,
    loadingSeconds,
    conversations,
    historyLoading,
    activeMeta,
    tripDayCount,
    tripDateRange,
    hasBudget,
    budgetDisplay,
    budgetCaption,
    perPersonBudget,
    hasPhotos,
    hasFood,
    visibleMapPoints,
    visibleHotelPoints,
    visibleRouteLines,
    mapLegendAttractions,
    mapLegendItems,
    resultSections,
    dayLabel,
    media,
    hotels,
    restaurants,
    foodPhotos,
    routes,
    loadPlan,
    openConversation,
    openConversationById,
    createTrip,
    sendAdjustment,
    loadConversations,
    deleteConversation,
    newTrip,
    resetTripState,
    saveTripMeta,
    removeTripMeta,
    metaFor,
    historySubtitle,
    downloadMarkdown,
    printPlan,
    handleError,
  }
}
