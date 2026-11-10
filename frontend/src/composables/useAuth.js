import { computed, reactive, ref } from 'vue'
import { api, setUnauthorizedHandler } from '../api/client'
import { getToken, setToken as persistToken } from '../utils/storage'

export function useAuth({ onSessionStart, onSessionClear, showToast }) {
  const token = ref(getToken())
  const user = ref(null)
  const booting = ref(Boolean(token.value))
  const authMode = ref('login')
  const authError = ref('')
  const authLoading = ref(false)
  const authForm = reactive({
    username: '',
    password: '',
    email: '',
    phone: '',
  })

  const avatarText = computed(() => {
    const name = user.value ? user.value.username : ''
    return name ? name.slice(0, 1).toUpperCase() : '?'
  })

  setUnauthorizedHandler(() => {
    if (!token.value) return
    clearSession()
    showToast('登录已失效，请重新登录', 'error')
  })

  function switchAuthMode(mode) {
    authMode.value = mode
    authError.value = ''
  }

  function startSession(nextToken, nextUser) {
    token.value = nextToken
    user.value = nextUser
    persistToken(nextToken)
    onSessionStart?.()
  }

  async function submitAuth() {
    authError.value = ''
    const { username, password, email, phone } = authForm

    if (!username || !password) {
      authError.value = '请填写账号和密码'
      return false
    }

    authLoading.value = true
    try {
      let data
      if (authMode.value === 'login') {
        data = await api.login(username, password)
      } else {
        const payload = { username, password }
        if (email) payload.email = email
        if (phone) payload.phone = phone
        data = await api.register(payload)
      }
      startSession(data.token, data.user)
      authForm.username = ''
      authForm.password = ''
      authForm.email = ''
      authForm.phone = ''
      return true
    } catch (e) {
      authError.value = e.message
      return false
    } finally {
      authLoading.value = false
    }
  }

  async function bootstrap() {
    if (!token.value) {
      booting.value = false
      return
    }
    booting.value = true
    try {
      user.value = await api.currentUser(token.value)
      onSessionStart?.()
    } catch (e) {
      clearSession()
    } finally {
      booting.value = false
    }
  }

  function clearSession() {
    token.value = ''
    user.value = null
    persistToken('')
    onSessionClear?.()
  }

  function logout() {
    clearSession()
    showToast('已退出登录', 'info')
  }

  return {
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
    bootstrap,
    logout,
    clearSession,
  }
}
