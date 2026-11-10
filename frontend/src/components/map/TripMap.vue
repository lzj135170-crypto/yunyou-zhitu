<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppTabs from '../ui/AppTabs.vue'
import Icon from '../ui/Icon.vue'
import { escapeHtml, isHttpUrl } from '../../utils/format'
import { routeModeColor } from '../../utils/routeModes'

const props = defineProps({
  points: { type: Array, default: () => [] },
  hotels: { type: Array, default: () => [] },
  routes: { type: Array, default: () => [] },
  fallback: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const container = ref(null)
const map = ref(null)
const markers = ref([])
const hotelMarkers = ref([])
const routeLines = ref([])
let infoWindows = []
const jsReady = ref(false)
const jsError = ref('')
const initializing = ref(false)
const forceStatic = ref(false)
const renderScheduled = ref(false)
const fallbackFading = ref(false)
let fallbackTimer = null
let viewInitialized = false
let routeFadeFrame = null

function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const hasPoints = computed(() => Array.isArray(props.points) && props.points.length > 0)

const hasGeometry = computed(
  () =>
    hasPoints.value ||
    (Array.isArray(props.hotels) && props.hotels.length > 0) ||
    (Array.isArray(props.routes) && props.routes.length > 0)
)

const geometryVersion = computed(() => {
  const points = (props.points || [])
    .map((point) => `${point.name}:${point.longitude},${point.latitude}`)
    .join('|')
  const hotels = (props.hotels || [])
    .map((hotel) => `${hotel.name}:${hotel.longitude},${hotel.latitude}`)
    .join('|')
  const routes = (props.routes || [])
    .map((route) => {
      const polyline = route.polyline || []
      const first = polyline[0] || []
      const last = polyline[polyline.length - 1] || []
      return `${route.mode}:${route.estimated ? 1 : 0}:${polyline.length}:${first[0]},${first[1]}:${last[0]},${last[1]}`
    })
    .join('|')
  return `${points}#${hotels}#${routes}`
})

let amapScriptPromise = null

function loadAmapScript(key) {
  if (window.AMap) return Promise.resolve()
  if (amapScriptPromise) return amapScriptPromise

  // 强制栅格底图：部分公司网络会拦截矢量瓦片域名。
  // 官方开关的拼写就是 forbidenWebGL（少一个 d），必须在脚本加载前设置。
  window.forbidenWebGL = true

  amapScriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-amap-loader="true"]')
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true })
      existing.addEventListener('error', () => reject(new Error('高德地图脚本加载失败')), {
        once: true,
      })
      return
    }

    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`
    script.async = true
    script.defer = true
    script.dataset.amapLoader = 'true'
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('高德地图脚本加载失败'))
    document.head.appendChild(script)
  }).then(() => {
    if (!window.AMap) throw new Error('高德地图脚本没有加载成功')
  })

  return amapScriptPromise
}

function routeView() {
  const coordinates = []
  const push = (lng, lat) => {
    const lngValue = Number(lng)
    const latValue = Number(lat)
    if (Number.isFinite(lngValue) && Number.isFinite(latValue)) {
      coordinates.push([lngValue, latValue])
    }
  }

  ;(props.points || []).forEach((point) => push(point.longitude, point.latitude))
  ;(props.hotels || []).forEach((hotel) => push(hotel.longitude, hotel.latitude))
  ;(props.routes || []).forEach((route) => {
    ;(route.polyline || []).forEach((point) => push(point[0], point[1]))
  })

  if (!coordinates.length) return null

  const lngs = coordinates.map(([lng]) => lng)
  const lats = coordinates.map(([, lat]) => lat)
  const center = [
    (Math.min(...lngs) + Math.max(...lngs)) / 2,
    (Math.min(...lats) + Math.max(...lats)) / 2,
  ]
  const span = Math.max(
    Math.max(...lngs) - Math.min(...lngs),
    Math.max(...lats) - Math.min(...lats)
  )
  let zoom = 13
  if (span > 0.3) zoom = 10
  else if (span > 0.12) zoom = 11
  else if (span > 0.05) zoom = 12
  return { center, zoom }
}

function routeColor(mode) {
  return routeModeColor(mode)
}

function photoHtml(url) {
  if (!isHttpUrl(url)) return ''
  return (
    `<img class="amap-trip-info__photo" src="${escapeHtml(url)}" alt="" ` +
    `loading="lazy" decoding="async" referrerpolicy="no-referrer">`
  )
}

function clearFallbackTimer() {
  if (fallbackTimer) {
    clearTimeout(fallbackTimer)
    fallbackTimer = null
  }
}

function scheduleRender() {
  if (renderScheduled.value) return
  renderScheduled.value = true
  nextTick(() => {
    renderScheduled.value = false
    initOrRender()
  })
}

async function initOrRender() {
  if (!hasGeometry.value) {
    destroyMap()
    return
  }
  if (!map.value) {
    await initMap()
    return
  }
  renderOverlays()
}

async function initMap() {
  if (initializing.value || map.value) return

  const key = window.TRAVEL_AMAP_JS_KEY || ''
  if (!key) {
    jsError.value = '未配置高德 JS API key'
    return
  }

  initializing.value = true
  jsError.value = ''
  try {
    await loadAmapScript(key)
    await nextTick()
    if (!container.value) return
    if (!window.AMap.TileLayer) throw new Error('高德瓦片图层不可用')

    // 底图走同源代理，浏览器只需要能访问后端；路径不带 amap/tile 关键字，
    // 避免被广告拦截规则误伤。
    const tileLayer = new window.AMap.TileLayer({
      getTileUrl: (x, y, z) => {
        if (x && typeof x === 'object') {
          z = x.z
          y = x.y
          x = x.x
        }
        return `/api/map/raster?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}&z=${encodeURIComponent(z)}`
      },
      zIndex: 10,
    })

    // 先用最终视野创建地图，避免先按默认 zoom 预取一整批瓦片再取消。
    const view = routeView()
    const mapOptions = {
      zoom: view ? view.zoom : 11,
      zooms: [3, 18],
      viewMode: '2D',
      resizeEnable: true,
      layers: [tileLayer],
    }
    if (view) mapOptions.center = view.center

    map.value = new window.AMap.Map(container.value, mapOptions)
    renderOverlays()
    jsReady.value = true
    nextTick(() => {
      if (map.value) map.value.resize()
    })
  } catch (e) {
    jsError.value = (e && e.message) || '交互地图加载失败'
  } finally {
    initializing.value = false
  }
}

function renderOverlays() {
  if (!map.value || !window.AMap) return

  if (routeFadeFrame !== null) {
    cancelAnimationFrame(routeFadeFrame)
    routeFadeFrame = null
  }
  infoWindows.forEach((info) => { if (info.getIsOpen()) info.close() })
  infoWindows = []
  routeLines.value.forEach((line) => map.value.remove(line))
  routeLines.value = []
  hotelMarkers.value.forEach((marker) => map.value.remove(marker))
  hotelMarkers.value = []
  markers.value.forEach((marker) => map.value.remove(marker))
  markers.value = []

  const attractionMarkers = []
  const routePath = []
  const points = (props.points || []).filter((point) => {
    const lng = Number(point.longitude)
    const lat = Number(point.latitude)
    return Number.isFinite(lng) && Number.isFinite(lat)
  })

  points.forEach((point, index) => {
    const position = [Number(point.longitude), Number(point.latitude)]
    routePath.push(position)

    const label = String.fromCharCode(65 + (index % 26))
    const marker = new window.AMap.Marker({
      position,
      title: point.name || '',
      offset: new window.AMap.Pixel(-15, -15),
      content:
        `<div class="amap-trip-marker" style="animation-delay:${Math.min(index * 40, 400)}ms">` +
        `${label}</div>`,
    })

    const pointPhoto = photoHtml(point.photo_url)
    const info = new window.AMap.InfoWindow({
      isCustom: true,
      closeWhenClickMap: true,
      offset: new window.AMap.Pixel(0, -18),
      content:
        `<div class="amap-trip-info">${pointPhoto}` +
        `<strong>${escapeHtml(point.name)}</strong>` +
        `<span>${escapeHtml(point.address || '')}</span></div>`,
    })
    if (pointPhoto && window.AMap.Size) {
      info.setSize(new window.AMap.Size(204, 182))
    }
    infoWindows.push(info)
    marker.on('click', () => info.open(map.value, position))
    attractionMarkers.push(marker)
  })

  if (attractionMarkers.length) {
    map.value.add(attractionMarkers)
    markers.value = attractionMarkers
  }

  renderHotelMarkers()
  renderRouteLines(routePath)
  applyRouteView()
}

function renderHotelMarkers() {
  const nextMarkers = []
  ;(props.hotels || []).forEach((hotel) => {
    const lng = Number(hotel.longitude)
    const lat = Number(hotel.latitude)
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return
    const position = [lng, lat]

    const marker = new window.AMap.Marker({
      position,
      title: hotel.name || '酒店',
      offset: new window.AMap.Pixel(-15, -15),
      content: '<div class="amap-hotel-marker" style="animation-delay:80ms">H</div>',
      zIndex: 120,
    })

    const rating =
      hotel.rating !== null && hotel.rating !== undefined
        ? `<span>${escapeHtml(String(hotel.rating))} 分</span>`
        : ''
    const hotelPhoto = photoHtml(hotel.photo_url)
    const info = new window.AMap.InfoWindow({
      isCustom: true,
      closeWhenClickMap: true,
      offset: new window.AMap.Pixel(0, -18),
      content:
        `<div class="amap-trip-info">${hotelPhoto}` +
        `<strong>${escapeHtml(hotel.name || '酒店')}</strong>` +
        rating +
        `<span>${escapeHtml(hotel.address || '')}</span></div>`,
    })
    if (hotelPhoto && window.AMap.Size) {
      info.setSize(new window.AMap.Size(204, 182))
    }
    infoWindows.push(info)
    marker.on('click', () => info.open(map.value, position))
    nextMarkers.push(marker)
  })

  if (nextMarkers.length) {
    map.value.add(nextMarkers)
    hotelMarkers.value = nextMarkers
  }
}

function renderRouteLines(fallbackPath) {
  const routes = Array.isArray(props.routes) ? props.routes : []
  const lines = []
  const fadeTargets = []
  const reduce = prefersReducedMotion()

  if (routes.length) {
    routes.forEach((route, index) => {
      const path = (route.polyline || [])
        .map((point) => [Number(point[0]), Number(point[1])])
        .filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat))
      if (path.length < 2) return

      const estimated = !!route.estimated
      const targetOpacity = estimated ? 0.75 : 0.9
      const options = {
        path,
        strokeColor: estimated ? '#929292' : routeColor(route.mode),
        strokeWeight: estimated ? 4 : 5,
        strokeOpacity: reduce ? targetOpacity : 0,
        strokeStyle: estimated ? 'dashed' : 'solid',
        lineJoin: 'round',
        lineCap: 'round',
        showDir: !estimated,
        zIndex: 40 + index,
      }
      if (estimated) options.strokeDasharray = [8, 6]
      const line = new window.AMap.Polyline(options)
      lines.push(line)
      fadeTargets.push({ line, opacity: targetOpacity })
    })
  } else if (fallbackPath.length >= 2) {
    const line = new window.AMap.Polyline({
      path: fallbackPath,
      strokeColor: '#666666',
      strokeWeight: 3,
      strokeOpacity: reduce ? 0.8 : 0,
      strokeStyle: 'dashed',
      strokeDasharray: [10, 6],
      lineJoin: 'round',
      lineCap: 'round',
      showDir: true,
      zIndex: 50,
    })
    lines.push(line)
    fadeTargets.push({ line, opacity: 0.8 })
  }

  if (lines.length) {
    map.value.add(lines)
    routeLines.value = lines
    if (!reduce) fadeRouteLines(fadeTargets)
  }
}

function fadeRouteLines(targets) {
  if (routeFadeFrame !== null) cancelAnimationFrame(routeFadeFrame)
  const startedAt = performance.now()
  const duration = 320

  function tick(now) {
    const progress = Math.min(1, (now - startedAt) / duration)
    const eased = 1 - Math.pow(1 - progress, 3)
    targets.forEach(({ line, opacity }) => {
      line.setOptions({ strokeOpacity: opacity * eased })
    })
    if (progress < 1) {
      routeFadeFrame = requestAnimationFrame(tick)
    } else {
      routeFadeFrame = null
    }
  }

  routeFadeFrame = requestAnimationFrame(tick)
}

function applyRouteView() {
  if (!map.value) return
  const view = routeView()
  if (!view) return

  const current = map.value.getCenter()
  const sameCenter =
    current &&
    Math.abs(current.lng - view.center[0]) < 1e-6 &&
    Math.abs(current.lat - view.center[1]) < 1e-6
  const sameZoom = Math.abs(map.value.getZoom() - view.zoom) < 0.1
  if (sameCenter && sameZoom) {
    viewInitialized = true
    return
  }

  const animate = viewInitialized && !prefersReducedMotion()
  try {
    if (animate) map.value.setZoomAndCenter(view.zoom, view.center, false, 320)
    else map.value.setZoomAndCenter(view.zoom, view.center)
  } catch {
    map.value.setZoomAndCenter(view.zoom, view.center)
  }
  viewInitialized = true
}

function onModeChange(value) {
  forceStatic.value = value === 'static'
  if (!forceStatic.value) {
    nextTick(() => {
      if (map.value) map.value.resize()
    })
  }
}

function destroyMap() {
  clearFallbackTimer()
  fallbackFading.value = false
  viewInitialized = false
  if (routeFadeFrame !== null) {
    cancelAnimationFrame(routeFadeFrame)
    routeFadeFrame = null
  }
  infoWindows.forEach((info) => { if (info.getIsOpen()) info.close() })
  infoWindows = []
  if (map.value) {
    routeLines.value.forEach((line) => map.value.remove(line))
    hotelMarkers.value.forEach((marker) => map.value.remove(marker))
    markers.value.forEach((marker) => map.value.remove(marker))
  }
  routeLines.value = []
  hotelMarkers.value = []
  markers.value = []
  if (map.value) {
    map.value.destroy()
    map.value = null
  }
  jsReady.value = false
}

watch(geometryVersion, scheduleRender)
watch(jsReady, (ready) => {
  clearFallbackTimer()
  if (!ready) {
    fallbackFading.value = false
    return
  }
  if (!props.fallback) return
  fallbackFading.value = true
  fallbackTimer = setTimeout(() => {
    fallbackFading.value = false
    fallbackTimer = null
  }, 400)
})
onMounted(initOrRender)
onBeforeUnmount(() => {
  clearFallbackTimer()
  destroyMap()
})
</script>

<template>
  <div class="trip-map">
    <template v-if="hasGeometry || fallback">
      <!-- 地图容器必须始终参与布局；用 v-show 而不是 v-if，避免高德在 0×0 视口初始化。 -->
      <div ref="container" class="trip-map__canvas" v-show="!forceStatic"></div>

      <!-- 打印不依赖高德 canvas，始终使用同一次请求返回的静态图。 -->
      <img
        v-if="fallback"
        class="trip-map__print"
        :src="fallback"
        alt="行程地图"
        decoding="async"
      />
      <p v-else class="trip-map__print-empty">
        交互地图没有生成可打印的静态图，景点顺序请参考下方图例。
      </p>

      <div
        v-if="fallback && (!jsReady || forceStatic || fallbackFading)"
        class="trip-map__fallback"
        :class="{ 'is-fading': jsReady && !forceStatic && fallbackFading }"
      >
        <img :src="fallback" alt="行程地图" />
      </div>

      <div v-if="!jsReady && !fallback" class="trip-map__overlay">
        <Icon name="map" :size="24" />
        <strong>{{ jsError ? '交互地图加载失败' : '正在加载交互地图…' }}</strong>
        <span>{{ jsError || '正在加载高德地图脚本' }}</span>
      </div>

      <div v-if="jsReady && fallback" class="trip-map__toggle">
        <AppTabs
          :model-value="forceStatic ? 'static' : 'interactive'"
          :tabs="[
            { id: 'interactive', label: '交互地图' },
            { id: 'static', label: '静态地图' },
          ]"
          variant="paper"
          aria-label="地图模式"
          @update:model-value="onModeChange"
        />
      </div>

      <p v-if="jsError && fallback" class="trip-map__note">交互地图不可用，已显示静态图</p>
    </template>

    <div v-else class="trip-map__placeholder">
      <Icon name="map" :size="26" />
      <strong>{{ loading ? '正在生成地图…' : error || '暂无可展示的点位' }}</strong>
      <span>{{ loading ? '高德地图正在解析景点坐标。' : '这次行程没有拿到可用的景点坐标。' }}</span>
    </div>
  </div>
</template>

<style scoped>
.trip-map {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 240px;
  background: var(--paper-deep);
}

.trip-map__canvas {
  width: 100%;
  height: 100%;
}

.trip-map__print,
.trip-map__print-empty {
  display: none;
}

.trip-map__fallback {
  position: absolute;
  inset: 0;
  opacity: 1;
  transition: opacity 400ms var(--ease-out);
}

.trip-map__fallback.is-fading {
  pointer-events: none;
  opacity: 0;
}

.trip-map__fallback img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.trip-map__overlay,
.trip-map__placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: var(--text-muted);
  text-align: center;
  background: var(--paper-deep);
}

.trip-map__overlay svg,
.trip-map__placeholder svg {
  color: var(--text-faint);
}

.trip-map__overlay strong,
.trip-map__placeholder strong {
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 600;
}

.trip-map__overlay span,
.trip-map__placeholder span {
  max-width: 42ch;
  color: var(--text-muted);
  font-size: var(--type-label);
}

.trip-map__toggle {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 5;
}

.trip-map__note {
  position: absolute;
  bottom: 10px;
  left: 10px;
  z-index: 5;
  padding: 4px 8px;
  color: var(--text-muted);
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-micro);
}
</style>
