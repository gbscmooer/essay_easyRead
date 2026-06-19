import { describe, it, expect, vi, beforeEach } from 'vitest'
import { get, post, upload, del } from '@/api/client'

/**
 * Build a mock `Response` the way client.ts actually consumes it: the client
 * reads the body via `res.text()` (then JSON.parses), and branches on `status`
 * (204 / empty body). So the mock must expose `text()`, not `json()`.
 */
function mockResponse(
  body: unknown,
  init: { ok?: boolean; status?: number; statusText?: string } = {},
): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? 'OK',
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  } as Response
}

describe('API Client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  describe('get', () => {
    it('should fetch data successfully', async () => {
      const mockData = { id: 1, name: 'test' }
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse(mockData))

      const result = await get('/papers/1')
      expect(result).toEqual(mockData)
      expect(fetch).toHaveBeenCalledWith('/api/papers/1', { signal: undefined })
    })

    it('should throw on error response', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        mockResponse(
          { detail: 'Paper not found', code: 'NOT_FOUND' },
          { ok: false, status: 404, statusText: 'Not Found' },
        ),
      )

      await expect(get('/papers/999')).rejects.toThrow('Paper not found')
    })
  })

  describe('post', () => {
    it('should post JSON data', async () => {
      const mockData = { message: 'success' }
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse(mockData))

      const result = await post('/analysis/config', { key: 'value' })
      expect(result).toEqual(mockData)
      expect(fetch).toHaveBeenCalledWith('/api/analysis/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'value' }),
      })
    })

    it('should post without body', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse({}))

      await post('/analysis/1/reparse')
      expect(fetch).toHaveBeenCalledWith('/api/analysis/1/reparse', {
        method: 'POST',
        headers: undefined,
        body: undefined,
      })
    })
  })

  describe('upload', () => {
    it('should upload file with FormData', async () => {
      const mockData = { id: 1, filename: 'test.pdf' }
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse(mockData))

      const file = new File(['test'], 'test.pdf', { type: 'application/pdf' })
      const result = await upload('/papers/upload', file)
      expect(result).toEqual(mockData)
    })
  })

  describe('del', () => {
    it('should send DELETE request', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse({ message: 'deleted' }))

      const result = await del('/papers/1')
      expect(result).toEqual({ message: 'deleted' })
      expect(fetch).toHaveBeenCalledWith('/api/papers/1', { method: 'DELETE' })
    })
  })
})
