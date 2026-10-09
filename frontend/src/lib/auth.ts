import { readonly, ref } from 'vue'
import type { Access } from './analytics'

export interface User { id: string; username: string; display_name: string; role: string; allowed_metrics?: string[]; permission_version?: number }
interface TokenResult { access_token: string; expires_in: number; user: User }
const currentUser = ref<User | null>(null)
const initializing = ref(true)
const sessionError = ref('')
let accessToken = ''
let generation = 0
let refreshFlight: Promise<void> | null = null
const csrfHeaders = { 'X-SalesMind-CSRF': '1' }
const accessListeners = new Set<() => void>()
let observedAccess = ''
let accessGeneration = 0
const responseGenerations = new WeakMap<Response, number>()
const latestPermissionVersions = new Map<string, number>()
export function getAccessGeneration() { return accessGeneration }
export function assertAccessGeneration(expected: number) { if (accessGeneration !== expected) throw new ApiError('账号权限已变化，请重新读取数据', 409) }
export function responseAccessGeneration(response: Response) { return responseGenerations.get(response) ?? accessGeneration }
export async function readAccessJson<T = any>(response: Response): Promise<T> {
  const expected = responseAccessGeneration(response)
  assertAccessGeneration(expected)
  const value = await response.json()
  assertAccessGeneration(expected)
  return value as T
}
export function invalidateAccess() { accessGeneration++; observedAccess = ''; accessListeners.forEach(listener => listener()) }
export function onAccessInvalidated(listener: () => void) { accessListeners.add(listener); return () => { accessListeners.delete(listener) } }
export function observeAccess(access: Access) {
  if (access.permission_version < (latestPermissionVersions.get(access.user_id) ?? 0)) throw new ApiError('账号权限已变化，请重新读取数据', 409)
  if (currentUser.value && access.user_id !== currentUser.value.id) throw new ApiError('账号已切换，请重新读取数据', 409)
  if (currentUser.value?.id === access.user_id && access.permission_version < (currentUser.value.permission_version ?? 0)) throw new ApiError('账号权限已变化，请重新读取数据', 409)
  const key = `${access.user_id}:${access.permission_version}:${access.scope_hash}`
  if (observedAccess && observedAccess !== key) invalidateAccess()
  observedAccess = key
  latestPermissionVersions.set(access.user_id, access.permission_version)
}

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

async function requireOk(response: Response) {
  if (response.ok) return response
  let detail = '服务暂时不可用，请稍后重试'
  try { const data = await response.json(); if (typeof data.detail === 'string') detail = data.detail } catch { /* 代理错误可能不是 JSON。 */ }
  throw new ApiError(detail, response.status)
}

function applyTokens(result: TokenResult, expectedGeneration: number) {
  if (generation !== expectedGeneration) throw new Error('会话已切换，请重新操作')
  if (!result.access_token || !result.user?.id) throw new Error('登录接口返回格式无效')
  if (currentUser.value && (currentUser.value.id !== result.user.id || currentUser.value.permission_version !== result.user.permission_version)) invalidateAccess()
  if (result.user.permission_version !== undefined) latestPermissionVersions.set(result.user.id, Math.max(result.user.permission_version, latestPermissionVersions.get(result.user.id) ?? 0))
  accessToken = result.access_token
  currentUser.value = result.user
  sessionError.value = ''
}

function clearSession(message = '') {
  generation++
  accessToken = ''
  currentUser.value = null
  invalidateAccess()
  sessionError.value = message
}

async function cookieLock<T>(action: () => Promise<T>): Promise<T> {
  // 多标签页共用 Cookie，浏览器支持时跨标签串行化刷新和退出。
  if (typeof navigator !== 'undefined' && navigator.locks) return navigator.locks.request('salesmind-auth-cookie', action)
  return action()
}

export function refreshAccess(): Promise<void> {
  if (refreshFlight) return refreshFlight
  const expected = generation
  refreshFlight = cookieLock(async () => {
    const response = await requireOk(await fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin', headers: csrfHeaders, cache: 'no-store' }))
    applyTokens(await response.json(), expected)
  }).catch(error => {
    if (generation === expected && error instanceof ApiError && error.status === 401) clearSession(currentUser.value ? '登录已失效，请重新登录' : '')
    throw error
  }).finally(() => { refreshFlight = null })
  return refreshFlight
}

export async function initializeAuth() {
  initializing.value = true
  try { await refreshAccess() }
  catch (error) { if (!(error instanceof ApiError && error.status === 401)) sessionError.value = error instanceof Error ? error.message : '无法恢复登录，请重试' }
  finally { initializing.value = false }
}

export async function login(body: { username: string; password: string; captcha_id: string; captcha_code: string }) {
  const expected = generation
  if (refreshFlight) await refreshFlight.catch(() => {})
  await cookieLock(async () => {
    const response = await requireOk(await fetch('/api/v1/auth/login', { method: 'POST', credentials: 'same-origin', headers: { ...csrfHeaders, 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' }))
    applyTokens(await response.json(), expected)
  })
}

export async function logout() {
  if (refreshFlight) await refreshFlight.catch(() => {})
  await cookieLock(async () => {
    await requireOk(await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin', headers: csrfHeaders, cache: 'no-store' }))
    clearSession()
  })
}

export async function fetchCaptcha(signal?: AbortSignal): Promise<{ captcha_id: string; image: string; expires_in: number }> {
  const response = await requireOk(await fetch('/api/v1/auth/captcha', { signal, cache: 'no-store', credentials: 'same-origin' }))
  return response.json()
}

export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const expected = generation
  if (!accessToken) await refreshAccess()
  const usedToken = accessToken
  let requestGeneration = accessGeneration
  const request = () => {
    if (generation !== expected) throw new Error('会话已切换，请重新登录')
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${accessToken}`)
    requestGeneration = accessGeneration
    return fetch(url, { ...init, headers, credentials: 'same-origin', cache: 'no-store' })
  }
  let response = await request()
  assertAccessGeneration(requestGeneration)
  if (response.status === 401) {
    if (accessToken === usedToken) await refreshAccess()
    response = await request()
    assertAccessGeneration(requestGeneration)
    if (response.status === 401) { clearSession('登录已失效，请重新登录'); throw new ApiError('登录已失效，请重新登录', 401) }
  }
  if ([403, 409].includes(response.status)) invalidateAccess()
  responseGenerations.set(response, accessGeneration)
  return response
}

export function useAuth() {
  return { user: readonly(currentUser), initializing: readonly(initializing), sessionError: readonly(sessionError), initializeAuth, login, logout }
}
