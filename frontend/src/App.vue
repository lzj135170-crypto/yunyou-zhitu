<script setup>
import { onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import AppHeader from './components/layout/AppHeader.vue'
import LoadingLoom from './components/loading/LoadingLoom.vue'
import AppDialog from './components/ui/AppDialog.vue'
import AppToast from './components/ui/AppToast.vue'
import ScrollProgress from './components/ui/ScrollProgress.vue'
import Spinner from './components/ui/Spinner.vue'
import AuthView from './views/AuthView.vue'
import HistoryView from './views/HistoryView.vue'
import PlanView from './views/PlanView.vue'
import ResultView from './views/ResultView.vue'
import { useAuth } from './composables/useAuth'
import { usePlanForm } from './composables/usePlanForm'
import { useToast } from './composables/useToast'
import { useTripResult } from './composables/useTripResult'
import { useViewRoute } from './composables/useViewRoute'

const { showToast } = useToast()
const pendingDelete = ref(null)
const deleting = ref(false)

let trip = null

const { view, activeId, navigate } = useViewRoute({ onNavigate: handleNavigate })

const auth = useAuth({
  onSessionStart: () => {
    trip?.loadConversations()
    if (view.value === 'result' && activeId.value) {
      trip?.openConversationById(activeId.value)
    }
  },
  onSessionClear: () => {
    trip?.resetTripState()
    navigate('plan')
  },
  showToast,
})
const {
  token,
  user,
  booting,
  authMode,
  authForm,
  authError,
  authLoading,
  avatarText,
  switchAuthMode,
  submitAuth,
  logout,
} = auth

trip = reactive(
  useTripResult({
    token,
    getUser: () => user.value,
    activeId,
    navigate,
    showToast,
  })
)

provide('trip', trip)
provide('navigate', navigate)

const {
  planForm,
  preferenceOptions,
  dietaryOptions,
  dayCount,
  togglePreference,
  validate,
  composePrompt,
  snapshot,
} = usePlanForm({ showToast })

// 打印前展开所有 details（天气说明、路线备选），打印后恢复用户原来的折叠状态。
// 行程和完整回复由 print.css 强制展开，不依赖这里的 DOM 操作。
let printDetails = []

function onBeforePrint() {
  printDetails = Array.from(document.querySelectorAll('details')).map((element) => [
    element,
    element.open,
  ])
  printDetails.forEach(([element]) => {
    element.open = true
  })
}

function onAfterPrint() {
  printDetails.forEach(([element, open]) => {
    element.open = open
  })
  printDetails = []
}

onMounted(() => {
  auth.bootstrap()
  window.addEventListener('beforeprint', onBeforePrint)
  window.addEventListener('afterprint', onAfterPrint)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeprint', onBeforePrint)
  window.removeEventListener('afterprint', onAfterPrint)
})

watch(
  [user, view],
  () => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  },
  { flush: 'post' }
)

function handleNavigate(state) {
  if (!trip) return
  if (state.view === 'result' && state.id) {
    trip.openConversationById(state.id)
  } else if (state.view === 'history') {
    trip.loadConversations()
  }
}

function focusMain() {
  // 用 #main 作为 href 会被 hash 路由误判成视图切换；
  // 拦截点击，只把焦点送到主区域。
  document.getElementById('main')?.focus()
}

function onHeaderNavigate(next) {
  if (next === 'result') {
    if (!trip.activeId) {
      showToast('先规划一次行程，再来看结果页', 'info')
      return
    }
    navigate('result', trip.activeId)
    return
  }
  navigate(next)
}

function onCreateTrip() {
  if (!validate()) return
  trip.createTrip(snapshot(), composePrompt())
}

function requestDelete(conv) {
  pendingDelete.value = conv
}

async function confirmDelete() {
  if (!pendingDelete.value || deleting.value) return
  const conv = pendingDelete.value
  const wasActive = trip.activeId === conv.id
  deleting.value = true
  try {
    const ok = await trip.deleteConversation(conv)
    if (!ok) return
    pendingDelete.value = null
    if (wasActive) navigate('history')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="app-root">
    <a class="skip-link" href="#main" @click.prevent="focusMain">跳到主要内容</a>
    <ScrollProgress />

  <div v-if="booting" id="main" class="boot-screen" role="status" aria-live="polite" tabindex="-1">
    <Spinner :size="22" />
    <span>正在加载…</span>
  </div>

  <AuthView
    v-else-if="!user"
    :auth-mode="authMode"
    :auth-form="authForm"
    :auth-error="authError"
    :auth-loading="authLoading"
    @switch-mode="switchAuthMode"
    @submit="submitAuth"
  />

  <template v-else>
    <AppHeader
      :user="user"
      :avatar-text="avatarText"
      :view="view"
      :has-result="Boolean(trip.activeId)"
      :inert="trip.creatingTrip || trip.adjustLoading"
      @navigate="onHeaderNavigate"
      @logout="logout"
    />

    <span :key="`sweep-${view}`" class="view-sweep" aria-hidden="true"></span>

    <main
      id="main"
      class="container page"
      tabindex="-1"
      :inert="trip.creatingTrip || trip.adjustLoading"
    >
      <Transition name="view" mode="out-in">
        <div :key="view" class="view-frame">
          <PlanView
            v-if="view === 'plan'"
            :plan-form="planForm"
            :preference-options="preferenceOptions"
            :dietary-options="dietaryOptions"
            :day-count="dayCount"
            :creating-trip="trip.creatingTrip"
            @toggle-preference="togglePreference"
            @submit="onCreateTrip"
          />

          <ResultView v-else-if="view === 'result'" />

          <HistoryView
            v-else
            :conversations="trip.conversations"
            :loading="trip.historyLoading"
            :meta-for="trip.metaFor"
            :subtitle-for="trip.historySubtitle"
            @refresh="trip.loadConversations()"
            @open="trip.openConversation($event)"
            @delete="requestDelete($event)"
            @plan="navigate('plan')"
          />
        </div>
      </Transition>
    </main>

    <LoadingLoom
      v-if="trip.creatingTrip || trip.adjustLoading"
      :mode="trip.creatingTrip ? 'create' : 'adjust'"
      :seconds="trip.loadingSeconds"
    />

  </template>

  <AppToast />

  <AppDialog
    :open="Boolean(pendingDelete)"
    title="删除这次行程？"
    :description="
      pendingDelete
        ? `确定删除「${pendingDelete.title || '未命名行程'}」吗？它的聊天记录和行程数据会一并删除。`
        : ''
    "
    confirm-text="删除"
    cancel-text="取消"
    danger
    :busy="deleting"
    @cancel="pendingDelete = null"
    @confirm="confirmDelete"
  />
  </div>
</template>

<style scoped>
.app-root {
  min-height: 100vh;
}

.boot-screen {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.view-frame {
  min-width: 0;
}

.view-enter-active,
.view-leave-active {
  transition:
    opacity 320ms var(--ease-out),
    transform 320ms var(--ease-out);
}

.view-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.view-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.view-sweep {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 55;
  width: 100%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  pointer-events: none;
  transform-origin: left center;
  animation: view-sweep 360ms var(--ease-out) both;
}

@keyframes view-sweep {
  from {
    opacity: 0.85;
    transform: scaleX(0);
  }
  to {
    opacity: 0;
    transform: scaleX(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .view-sweep {
    display: none;
  }
}
</style>
