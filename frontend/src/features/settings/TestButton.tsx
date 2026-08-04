/**
 * API 连接测试按钮
 *
 * 点击后调用后端 test-connection，显示测试结果。
 * 成功显示耗时，失败弹出错误详情弹窗。
 */
import { useState, useCallback } from 'react'
import { post } from '@/api/client'
import { LoadingSpinner } from '@/ds-entry'


interface TestResult {
  ok: boolean
  message: string
  latency_ms: number | null
}


interface TestButtonProps {
  type: 'translate' | 'analysis' | 'embedding'
  api_key?: string
  base_url?: string
  model?: string
  provider?: string
}

export function TestButton({ type, api_key, base_url, model, provider }: TestButtonProps) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [showError, setShowError] = useState(false)

  const handleTest = useCallback(async () => {
    setTesting(true)
    setResult(null)
    setShowError(false)
    try {
      const res = await post<TestResult>('/analysis/test-connection', {
        type,
        api_key: api_key ?? '',
        base_url: base_url ?? '',
        model: model ?? '',
        provider: provider ?? '',
      })
      setResult(res)
    } catch (e: unknown) {
      setResult({ ok: false, message: e instanceof Error ? e.message : '请求失败', latency_ms: null })
    } finally {
      setTesting(false)
    }
  }, [type, api_key, base_url, model, provider])

  const btnStyle =
    result === null
      ? { borderColor: 'var(--border)', color: 'var(--text-secondary)', backgroundColor: 'transparent' }
      : result.ok
        ? { borderColor: 'color-mix(in srgb, var(--success) 50%, transparent)', color: 'var(--success)', backgroundColor: 'var(--success-subtle, color-mix(in srgb, var(--success) 10%, transparent))' }
        : { borderColor: 'color-mix(in srgb, var(--error) 50%, transparent)', color: 'var(--error)', backgroundColor: 'var(--error-subtle)', cursor: 'pointer' as const }

  return (
    <div className="flex items-center gap-2 relative">
      <button
        onClick={() => {
          if (result && !result.ok) {
            setShowError(v => !v)
          } else {
            handleTest()
          }
        }}
        disabled={testing}
        className="px-3 py-1 text-xs rounded-lg border transition-colors disabled:opacity-50"
        style={btnStyle}
      >
        {testing ? (
          <span className="flex items-center gap-1.5">
            <LoadingSpinner size="xs" />
            测试中
          </span>
        ) : result?.ok
          ? `测试通过${result.latency_ms != null ? ` (${result.latency_ms}ms)` : ''}`
          : result
            ? '测试失败'
            : '测试'}
      </button>

      {/* 失败时点击按钮弹出错误详情 */}
      {showError && result && !result.ok && (
        <>
          {/* 遮罩 */}
          <div
            className="fixed inset-0 z-50"
            onClick={() => setShowError(false)}
          />
          {/* 弹窗 */}
          <div
            className="absolute right-0 top-full mt-2 z-50 w-80 rounded-xl p-4 border"
            style={{
              backgroundColor: 'var(--bg-surface)',
              borderColor: 'var(--border)',
              backdropFilter: 'blur(20px)',
            }}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium" style={{ color: 'var(--error)' }}>
                连接失败
              </span>
              <button
                onClick={() => setShowError(false)}
                className="text-xs"
                style={{ color: 'var(--text-muted)' }}
              >
                &times;
              </button>
            </div>
            <p
              className="text-xs leading-relaxed break-all"
              style={{ color: 'var(--text-secondary)' }}
            >
              {result.message}
            </p>
            {result.latency_ms != null && (
              <p className="text-[11px] mt-2" style={{ color: 'var(--text-muted)' }}>
                耗时 {result.latency_ms}ms
              </p>
            )}
            <button
              onClick={() => { setShowError(false); handleTest() }}
              className="mt-3 px-3 py-1 text-xs rounded-lg border transition-colors"
              style={{
                borderColor: 'var(--border)',
                color: 'var(--text-secondary)',
                backgroundColor: 'var(--bg-elevated)',
              }}
            >
              重试
            </button>
          </div>
        </>
      )}
    </div>
  )
}
