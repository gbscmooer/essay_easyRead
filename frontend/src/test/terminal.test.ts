import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createTerminalSession, terminalWebSocketTarget } from '@/api/terminal'

function mockResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
  } as Response
}

describe('terminal access', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('requests a cancellable one-time session', async () => {
    const controller = new AbortController()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse({ ticket: 'session-ticket', expires_in: 30 }),
    )

    await expect(createTerminalSession(controller.signal)).resolves.toEqual({
      ticket: 'session-ticket',
      expires_in: 30,
    })
    expect(fetch).toHaveBeenCalledWith('/api/terminal/session', {
      method: 'POST',
      headers: undefined,
      body: undefined,
      signal: controller.signal,
    })
  })

  it('builds a secure websocket target and encodes cwd', () => {
    expect(terminalWebSocketTarget(
      'abc_123',
      '/Users/research/论文 & code',
      { protocol: 'https:', host: 'reader.example' },
    )).toEqual({
      url: 'wss://reader.example/api/terminal/ws?cwd=%2FUsers%2Fresearch%2F%E8%AE%BA%E6%96%87+%26+code',
      protocol: 'essay-terminal.abc_123',
    })
  })

  it('uses ws and omits an empty cwd query', () => {
    expect(terminalWebSocketTarget(
      'token',
      undefined,
      { protocol: 'http:', host: 'localhost:5173' },
    ).url).toBe('ws://localhost:5173/api/terminal/ws')
  })
})
