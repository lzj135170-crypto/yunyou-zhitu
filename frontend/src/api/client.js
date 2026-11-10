/**
 * 后端接口封装。
 *
 * 页面和接口同源（都由 FastAPI 在 8000 端口提供），所以路径直接用相对的 /api。
 * 后端有两层返回约定，封装时必须都处理：
 *   1. HTTP 状态码 —— 401、422、500
 *   2. 响应体里的 code —— 业务失败时 HTTP 可能仍是 200
 * 只判断其中一层都会漏。这里统一转成 ApiError，调用方只 catch 一种异常。
 */

const API_BASE = '/api'

export class ApiError extends Error {
  constructor(message, code) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

let unauthorizedHandler = null

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler
}

async function request(method, path, options = {}) {
  const {
    token,
    body,
    timeoutMs = 0,
    timeoutMessage = '请求超时，请稍后重试',
  } = options

  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json; charset=utf-8'
  if (token) headers.Authorization = `Bearer ${token}`

  // 路线计算可能打十几次高德，浏览器默认没有超时；显式给一个上限。
  let controller = null
  let timer = null
  if (timeoutMs > 0) {
    controller = new AbortController()
    timer = setTimeout(() => controller.abort(), timeoutMs)
  }

  let resp
  try {
    resp = await fetch(API_BASE + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller ? controller.signal : undefined,
    })
  } catch (e) {
    if (e && e.name === 'AbortError') {
      throw new ApiError(timeoutMessage, 0)
    }
    throw new ApiError('无法连接后端服务，请确认 uvicorn 已经启动', 0)
  } finally {
    if (timer) clearTimeout(timer)
  }

  // 先取文本再自己解析 JSON：后端某些异常返回的响应体不是 JSON。
  const text = await resp.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch (e) {
      payload = null
    }
  }

  if (!resp.ok) {
    if (resp.status === 401 && token && unauthorizedHandler) {
      unauthorizedHandler()
    }
    const msg = payload && typeof payload.message === 'string' ? payload.message : null
    throw new ApiError(msg || `请求失败（HTTP ${resp.status}）`, resp.status)
  }

  if (payload && typeof payload.code === 'number' && payload.code !== 200) {
    throw new ApiError(payload.message || '请求失败', payload.code)
  }

  return payload ? payload.data : null
}

export const api = {
  // ---------- 鉴权 ----------
  login: (username, password) =>
    request('POST', '/auth/login', { body: { username, password } }),

  register: (payload) => request('POST', '/auth/register', { body: payload }),

  currentUser: (token) => request('GET', '/user/info', { token }),

  // ---------- 会话 ----------
  listConversations: (token) => request('GET', '/conversations', { token }),

  getConversation: (token, id) =>
    request('GET', `/conversations/${id}`, { token }),

  createConversation: (token, title) =>
    request('POST', '/conversations', { token, body: title ? { title } : {} }),

  updateConversation: (token, id, title) =>
    request('PATCH', `/conversations/${id}`, { token, body: { title } }),

  deleteConversation: (token, id) =>
    request('DELETE', `/conversations/${id}`, { token }),

  // ---------- 消息 ----------
  listMessages: (token, id, { limit = 50, beforeId = null } = {}) => {
    let query = `/conversations/${id}/messages?limit=${limit}`
    if (beforeId !== null && beforeId !== undefined) {
      query += `&before_id=${beforeId}`
    }
    return request('GET', query, { token })
  },

  sendMessage: (token, id, content) =>
    request('POST', `/conversations/${id}/messages`, { token, body: { content } }),

  // ---------- 结果页 ----------
  getLatestPlan: (token, id) =>
    request('GET', `/conversations/${id}/plan`, { token }),

  getTripMedia: (token, id) =>
    request('GET', `/conversations/${id}/media`, { token }),

  getTripHotels: (token, id) =>
    request('GET', `/conversations/${id}/hotels`, {
      token,
      timeoutMs: 30000,
      timeoutMessage: '酒店信息加载超时，请稍后重试',
    }),

  getTripRestaurants: (token, id, { keyword, area = '', limit = 5 }) => {
    const params = new URLSearchParams({
      keyword,
      area: area || '',
      limit: String(limit),
    })
    return request('GET', `/conversations/${id}/restaurants?${params.toString()}`, {
      token,
      timeoutMs: 30000,
      timeoutMessage: '餐厅信息加载超时，请稍后重试',
    })
  },

  getTripRoutes: (token, id, payload) =>
    request('POST', `/conversations/${id}/routes`, {
      token,
      body: payload,
      timeoutMs: 60000,
      timeoutMessage: '路线计算超时，请稍后重试',
    }),
}
