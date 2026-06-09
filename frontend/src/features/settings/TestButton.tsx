/**
 * API 连接测试按钮
 *
 * 点击后调用后端 test-connection，显示测试结果。
 * 成功显示耗时，失败弹出错误详情弹窗。
 */
import { useState, useCallback } from 'react'
import { post } from '@/api/client'


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
        className={`px-3 py-1 text-xs rounded-lg border transition-colors disabled:opacity-50 ${
          result === null
            ? 'border-gray-600 text-gray-400 hover:border-gray-500 hover:text-gray-300'
            : result.ok
              ? 'border-green-500/50 text-green-400 bg-green-500/10'
              : 'border-red-500/50 text-red-400 bg-red-500/10 cursor-pointer'
        }`}
      >
        {testing ? (
          <span className="flex items-center gap-1.5">
            <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
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
            className="absolute right-0 top-full mt-2 z-50 w-80 rounded-xl p-4 border bg-gray-800 border-gray-700"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-red-400">连接失败</span>
              <button
                onClick={() => setShowError(false)}
                className="text-xs hover:opacity-80 text-gray-500"
              >
                &times;
              </button>
            </div>
            <p className="text-xs leading-relaxed break-all text-gray-300">
              {result.message}
            </p>
            {result.latency_ms != null && (
              <p className="text-[11px] mt-2 text-gray-500">
                耗时 {result.latency_ms}ms
              </p>
            )}
            <button
              onClick={() => { setShowError(false); handleTest() }}
              className="mt-3 px-3 py-1 text-xs rounded-lg border border-gray-700 text-gray-300 bg-gray-900 hover:bg-gray-800 transition-colors"
            >
              重试
            </button>
          </div>
        </>
      )}
    </div>
  )
}
