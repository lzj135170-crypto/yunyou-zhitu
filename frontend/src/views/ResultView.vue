<script setup>
import { computed, inject, nextTick, watch } from 'vue'
import AppButton from '../components/ui/AppButton.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import ResultRail from '../components/layout/ResultRail.vue'
import AnswerSection from '../components/result/AnswerSection.vue'
import AdjustSection from '../components/result/AdjustSection.vue'
import AttractionsSection from '../components/result/AttractionsSection.vue'
import BudgetSection from '../components/result/BudgetSection.vue'
import FoodSection from '../components/result/FoodSection.vue'
import HotelsSection from '../components/result/HotelsSection.vue'
import ItinerarySection from '../components/result/ItinerarySection.vue'
import MapSection from '../components/result/MapSection.vue'
import OverviewSection from '../components/result/OverviewSection.vue'
import ResultSkeleton from '../components/result/ResultSkeleton.vue'
import RoutesSection from '../components/result/RoutesSection.vue'
import SourcesSection from '../components/result/SourcesSection.vue'
import TipsSection from '../components/result/TipsSection.vue'
import TransportSection from '../components/result/TransportSection.vue'
import WeatherSection from '../components/result/WeatherSection.vue'
import { useScrollSpy } from '../composables/useScrollSpy'
import { scrollToId } from '../utils/scroll'

const trip = inject('trip')
const navigate = inject('navigate')

const sectionIds = computed(() => trip.resultSections.map((section) => section.id))
const { activeSection, setActive } = useScrollSpy(sectionIds)

const routableCount = computed(() => trip.routes.routablePlaces().length)
const hasEstimatedRoute = computed(() =>
  (trip.visibleRouteLines || []).some((line) => line.estimated)
)

watch(
  () => trip.activeId,
  async () => {
    await nextTick()
    setActive(trip.resultSections[0]?.id || 'overview')
  },
  { immediate: true }
)

function onRailSelect(id) {
  setActive(id)
  scrollToId(id)
}

function onRailAction(action) {
  if (action === 'plan') navigate('plan')
  else if (action === 'new') trip.newTrip()
  else if (action === 'history') navigate('history')
  else if (action === 'markdown') trip.downloadMarkdown()
  else if (action === 'print') trip.printPlan()
}

function onRetry() {
  if (trip.activeId) trip.loadPlan(trip.activeId)
}

function onHotelAddress(value) {
  trip.routes.hotelAddress = value
  trip.routes.onHotelAddressInput()
}

async function onSelectHotel(place) {
  trip.routes.selectHotel(place)
  await nextTick()
  scrollToId('routes')
}
</script>

<template>
  <div class="result-layout">
    <ResultRail
      :sections="trip.resultSections"
      :active-section="activeSection"
      @select="onRailSelect"
      @action="onRailAction"
    />

    <div class="result-main">
      <ResultSkeleton v-if="trip.planLoading && !trip.plan" />

      <div v-else-if="trip.plan" class="result-dashboard">
        <div class="dash-cell dash-cell--overview">
          <OverviewSection
            :plan="trip.plan"
            :meta="trip.activeMeta"
            :trip-date-range="trip.tripDateRange"
            :trip-day-count="trip.tripDayCount"
            :photo-map="trip.media.photoMap"
            :map-url="trip.media.mapUrl"
          />
        </div>

        <div class="dash-cell dash-cell--map">
          <MapSection
            :map-url="trip.media.mapUrl"
            :points="trip.visibleMapPoints"
            :hotels="trip.visibleHotelPoints"
            :routes="trip.visibleRouteLines"
            :loading="trip.media.mapLoading"
            :error="trip.media.mapError"
            :legend-attractions="trip.mapLegendAttractions"
            :legend-items="trip.mapLegendItems"
            :has-estimated="hasEstimatedRoute"
            :selected-day="trip.routes.selectedRouteDay"
            :route-days="trip.routes.routePlan?.days || []"
            @select-day="trip.routes.selectRouteDay($event)"
          />
        </div>

        <div class="dash-cell dash-cell--weather">
          <WeatherSection :weather="trip.plan.weather" />
        </div>

        <div class="dash-cell dash-cell--itinerary">
          <ItinerarySection
            :plan="trip.plan"
            :meta="trip.activeMeta"
            :day-label="trip.dayLabel"
            :selected-day="trip.routes.selectedRouteDay"
            :route-plan="trip.routes.routePlan"
            :photo-for="trip.media.photoFor"
            @select-day="trip.routes.selectRouteDay($event)"
          />
        </div>

        <div class="dash-cell dash-cell--attractions">
          <AttractionsSection
            :plan="trip.plan"
            :photo-for="trip.media.photoFor"
            :drop-photo="trip.media.dropPhoto"
            :has-photos="trip.hasPhotos"
            :map-points="trip.media.mapPoints"
          />
        </div>

        <div v-if="trip.hasFood" class="dash-cell dash-cell--food">
          <FoodSection
            :plan="trip.plan"
            :target="trip.restaurants.target"
            :places="trip.restaurants.places"
            :loading="trip.restaurants.loading"
            :error="trip.restaurants.error"
            :is-target="trip.restaurants.isTarget"
            :button-label="trip.restaurants.buttonLabel"
            :photo-failed="trip.restaurants.photoFailed"
            :food-photo-for="trip.foodPhotos.photoFor"
            :food-photo-loading="trip.foodPhotos.isLoading"
            :food-photo-failed="trip.foodPhotos.hasFailed"
            @toggle="trip.restaurants.toggle($event)"
            @close="trip.restaurants.clear()"
            @photo-error="trip.restaurants.onPhotoError"
            @load-photo="trip.foodPhotos.load($event)"
          />
        </div>

        <div class="dash-cell dash-cell--hotels">
          <HotelsSection
            :plan="trip.plan"
            :loading="trip.hotels.loading"
            :error="trip.hotels.error"
            :tiers-for="trip.hotels.tiersFor"
            :tier-key="trip.hotels.tierKey"
            :places-for="trip.hotels.placesFor"
            :tier-error="trip.hotels.tierError"
            :select-tier="trip.hotels.selectTier"
            :photo-failed="trip.hotels.photoFailed"
            :on-photo-error="trip.hotels.onPhotoError"
            @select-hotel="onSelectHotel"
          />
        </div>

        <div class="dash-cell dash-cell--routes">
          <RoutesSection
            :route-plan="trip.routes.routePlan"
            :loading="trip.routes.routesLoading"
            :error="trip.routes.routesError"
            :warnings="trip.routes.routeWarnings"
            :selected-day="trip.routes.selectedRouteDay"
            :days-to-show="trip.routes.routeDaysToShow"
            :hotel-address="trip.routes.hotelAddress"
            :selected-hotel="trip.routes.selectedHotel"
            :stale="trip.routes.routesStale"
            :routable-count="routableCount"
            @generate="trip.routes.generateRoutes()"
            @clear-hotel="trip.routes.clearHotel()"
            @update-address="onHotelAddress"
            @select-day="trip.routes.selectRouteDay($event)"
            @scroll-map="scrollToId('map')"
          />
        </div>

        <div class="dash-cell dash-cell--transport">
          <TransportSection :transport="trip.plan.transport" />
        </div>

        <div class="dash-cell dash-cell--budget">
          <BudgetSection
            :meta="trip.activeMeta"
            :trip-day-count="trip.tripDayCount"
            :has-budget="trip.hasBudget"
            :budget-display="trip.budgetDisplay"
            :budget-caption="trip.budgetCaption"
            :per-person-budget="trip.perPersonBudget"
            :budget-assumption="trip.plan.budget_assumption"
          />
        </div>

        <div class="dash-cell dash-cell--tips">
          <TipsSection :tips="trip.plan.tips" :warnings="trip.plan.warnings" />
        </div>

        <div class="dash-cell dash-cell--sources">
          <SourcesSection :sources="trip.plan.sources" />
        </div>

        <div class="dash-cell dash-cell--answer">
          <AnswerSection
            :answer="trip.plan.answer"
            :show-answer="trip.showAnswer"
            @toggle="trip.showAnswer = !trip.showAnswer"
          />
        </div>

        <div class="dash-cell dash-cell--adjust">
          <AdjustSection
            :model-value="trip.adjustText"
            :loading="trip.adjustLoading"
            @update:model-value="trip.adjustText = $event"
            @submit="trip.sendAdjustment()"
          />
        </div>
      </div>

      <div v-else class="result-empty">
        <EmptyState
          icon="compass"
          :title="trip.planError || '还没有可展示的行程'"
          description="先回到规划页生成一次，或者从历史列表打开已有行程。"
        >
          <AppButton v-if="trip.planError" variant="primary" arrow @click="onRetry">
            重试加载
          </AppButton>
          <AppButton variant="secondary" @click="navigate('plan')">去规划页</AppButton>
        </EmptyState>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-layout {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: clamp(28px, 4vw, 56px);
  align-items: start;
  min-width: 0;
  padding-top: 28px;
}

.result-main {
  min-width: 0;
}

/* 一个服务占一整行；服务内部再用横向轨道排卡片。
 * 不把两个服务左右并排，避免窄窗口下每个服务被压成一条。 */
.result-dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  row-gap: 0;
  align-items: start;
}

.dash-cell {
  min-width: 0;
}

.dash-cell > :deep(.result-section) {
  width: 100%;
}

.result-empty {
  padding-top: 20px;
}

@media (max-width: 980px) {
  .result-layout {
    grid-template-columns: 1fr;
    gap: 16px;
  }
}
</style>
