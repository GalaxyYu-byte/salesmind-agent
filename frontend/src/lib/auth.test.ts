import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const user = { id: 'user-1', username: 'tester', display_name: '测试用户', role: 'SALES_DIRECTOR' }
const body = { username: 'tester', password: 'test-only-password', captcha_id: 'id', captcha_code: 'AAAA' }
const tokens = (token: string) => new Response(JSON.stringify({ access_token: token, expires_in: 1800, user }))
const denied = (status: number) => new Response(JSON.stringify({ detail: status === 401 ? '登录已过期' : '连接失败' }), { status })
beforeEach(() => { vi.resetModules() })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('authentication lifecycle', () => {
  it('restores login by cookie without reading persisted tokens', async () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('token must not be read') } })
    const fetchMock = vi.fn().mockResolvedValue(tokens('restored'))
    vi.stubGlobal('fetch', fetchMock)
    const auth = await import('./auth')
    await auth.initializeAuth()
    expect(auth.useAuth().user.value).toEqual(user)
    expect(auth.useAuth().initializing.value).toBe(false)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/refresh', expect.objectContaining({ method: 'POST', credentials: 'same-origin', headers: { 'X-SalesMind-CSRF': '1' } }))
  })

  it('treats absent refresh cookie as logged out and masks no new state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(denied(401)))
    const auth = await import('./auth')
    await auth.initializeAuth()
    expect(auth.useAuth().user.value).toBeNull()
    expect(auth.useAuth().sessionError.value).toBe('')
  })

  it('adds Authorization and preserves cancellation on sales requests', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(tokens('access-1')).mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    const auth = await import('./auth')
    await auth.login(body)
    const controller = new AbortController()
    await auth.authFetch('/api/v1/sales/overview', { signal: controller.signal })
    const init = fetchMock.mock.calls[1]![1]
    expect(init.headers.get('Authorization')).toBe('Bearer access-1')
    expect(init.signal).toBe(controller.signal)
    expect(init.cache).toBe('no-store')
  })

  it('shares one refresh for concurrent 401s and retries with new access', async () => {
    let refreshCount = 0
    let queryCount = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/login')) return tokens('old')
      if (url.endsWith('/refresh')) { refreshCount++; return tokens('new') }
      queryCount++
      return new Headers(init.headers).get('Authorization') === 'Bearer old' ? denied(401) : new Response('success')
    }))
    const auth = await import('./auth'); await auth.login(body)
    const results = await Promise.all([auth.authFetch('/api/v1/sales/overview'), auth.authFetch('/api/v1/sales/overview')])
    expect(results.every(r => r.ok)).toBe(true)
    expect(refreshCount).toBe(1)
    expect(queryCount).toBe(4)
  })

  it('returns to login when refresh is invalid without retry loops', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(tokens('old')).mockResolvedValue(denied(401))
    vi.stubGlobal('fetch', fetchMock)
    const auth = await import('./auth'); await auth.login(body)
    await expect(auth.authFetch('/api/v1/sales/overview')).rejects.toThrow('登录已过期')
    expect(auth.useAuth().user.value).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('does not loop when the retried request still returns 401', async () => {
    let count = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      count++
      return url.endsWith('/login') || url.endsWith('/refresh') ? tokens('new') : denied(401)
    }))
    const auth = await import('./auth'); await auth.login(body)
    await expect(auth.authFetch('/api/v1/sales/overview')).rejects.toThrow('登录已失效')
    expect(auth.useAuth().user.value).toBeNull()
    expect(count).toBe(4)
  })

  it('retains login on transient refresh failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(tokens('old')).mockResolvedValueOnce(denied(401)).mockResolvedValue(denied(503)))
    const auth = await import('./auth'); await auth.login(body)
    await expect(auth.authFetch('/api/v1/sales/overview')).rejects.toThrow('连接失败')
    expect(auth.useAuth().user.value).toEqual(user)
  })

  it('retains login if server logout fails and clears only after success', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(tokens('old')).mockResolvedValueOnce(denied(503)).mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const auth = await import('./auth'); await auth.login(body)
    await expect(auth.logout()).rejects.toThrow('连接失败')
    expect(auth.useAuth().user.value).toEqual(user)
    await auth.logout()
    expect(auth.useAuth().user.value).toBeNull()
  })

  it('waits for an in-flight rotation before revoking its resulting cookie', async () => {
    let finish!: (r: Response) => void
    const pending = new Promise<Response>(resolve => { finish = resolve })
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(url)
      if (url.endsWith('/login')) return tokens('old')
      if (url.endsWith('/refresh')) return pending
      return new Response(null, { status: 204 })
    }))
    const auth = await import('./auth'); await auth.login(body)
    const refresh = auth.refreshAccess()
    const exit = auth.logout()
    expect(calls.some(url => url.endsWith('/logout'))).toBe(false)
    finish(tokens('new'))
    await Promise.all([refresh, exit])
    expect(calls.at(-1)).toBe('/api/v1/auth/logout')
    expect(auth.useAuth().user.value).toBeNull()
  })
})

describe('permission response invalidation', () => {
  it('rejects a pending old response after another request observes revoked access', async () => {
    let complete!: (response: Response) => void
    const delayed = new Promise<Response>(resolve => { complete = resolve })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/login')) return tokens('access')
      if (url.includes('/artifacts/')) return delayed
      return denied(403)
    }))
    const auth = await import('./auth')
    await auth.login(body)
    const stale = auth.authFetch('/api/v1/artifacts/old')
    const rejected = expect(stale).rejects.toMatchObject({ status: 409 })
    expect((await auth.authFetch('/api/v1/sales/overview')).status).toBe(403)
    complete(new Response(JSON.stringify({ gross_profit: '987654.00' })))
    await rejected
  })

  it('rejects old JSON when permissions change while the response body is being read', async () => {
    let complete!: (value: unknown) => void
    const delayed = new Promise<unknown>(resolve => { complete = resolve })
    const response = new Response('{}')
    vi.spyOn(response, 'json').mockImplementation(() => delayed)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(tokens('access')).mockResolvedValue(response))
    const auth = await import('./auth')
    await auth.login(body)
    const received = await auth.authFetch('/api/v1/artifacts/old')
    const stale = auth.readAccessJson(received)
    const rejected = expect(stale).rejects.toMatchObject({ status: 409 })
    auth.invalidateAccess()
    complete({ gross_profit: '987654.00' })
    await rejected
  })

  it('rejects a result with an older permission version or a different account', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(tokens('access')))
    const auth = await import('./auth')
    await auth.login(body)
    const access = { user_id: user.id, permission_version: 2, scope_hash: 'scope-2', scope_label: '授权区域', allowed_metrics: ['sales_amount'] }
    auth.observeAccess(access)
    expect(() => auth.observeAccess({ ...access, permission_version: 1 })).toThrow('权限已变化')
    expect(() => auth.observeAccess({ ...access, user_id: 'other-user' })).toThrow('账号已切换')
  })
})
