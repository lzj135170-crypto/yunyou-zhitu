export const TOKEN_KEY = 'travel_token'
export const TRIP_META_KEY = 'travel_trip_meta_v1'
export const HOTEL_META_KEY = 'travel_hotel_meta_v1'

function readObject(key) {
  try {
    const raw = localStorage.getItem(key)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch (e) {
    return {}
  }
}

function writeObject(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch (e) {
    // 隐私模式 / 存储满时失败不影响主流程。
    return false
  }
}

export function readTripMeta() {
  return readObject(TRIP_META_KEY)
}

export function writeTripMeta(value) {
  return writeObject(TRIP_META_KEY, value)
}

export function readHotelMeta() {
  return readObject(HOTEL_META_KEY)
}

export function writeHotelMeta(value) {
  return writeObject(HOTEL_META_KEY, value)
}

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch (e) {
    return ''
  }
}

export function setToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch (e) {
    // 存储不可用时只在内存里保留 token。
  }
}

export function hotelMetaKey(user, conversationId) {
  const userId = (user && (user.user_id || user.username)) || 'guest'
  return `${userId}:${conversationId || ''}`
}
