/**
 * 界面配置区块
 *
 * 主题、字体、高亮时长、PDF 缩放参数。
 */
import { useState } from 'react'
import { setUIPreview } from '@/hooks/useConfig'
import { ThemeEditor } from './ThemeEditor'
import type { UIConfig } from './config'


const FONTS = [
  { key: 'sans', label: 'Arial（非衬线）' },
  { key: 'serif', label: 'Times New Roman（衬线）' },
  { key: 'mono', label: '等宽字体' },
  { key: 'custom', label: '自定义字体' },
]


interface UIConfigSectionProps {
  config: UIConfig
  onChange: (cfg: UIConfig) => void
}

export default function UIConfigSection({ config, onChange }: UIConfigSectionProps) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-gray-100">界面设置</h2>
        <p className="text-xs text-gray-500 mt-0.5">主题、字体和交互行为参数</p>
      </div>

      {/* 主题选择 + 自定义编辑器 */}
      <ThemeEditor config={config} onChange={onChange} />

      {/* 字体选择 */}
      <div>
        <label className="block text-xs text-gray-500 mb-2">界面字体</label>
        <div className="space-y-1.5">
          {FONTS.map(font => (
            <button
              key={font.key}
              onClick={() => { onChange({ ...config, font: font.key }); setUIPreview({ font: font.key }) }}
              className={`w-full text-left px-3 py-1.5 text-sm rounded border transition-colors ${
                config.font === font.key
                  ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              {font.label}
            </button>
          ))}
        </div>
        {config.font === 'custom' && (
          <input
            type="text"
            value={config.custom_font}
            onChange={e => { onChange({ ...config, custom_font: e.target.value }); setUIPreview({ custom_font: e.target.value }) }}
            placeholder="输入字体名称，如 PingFang SC"
            className="mt-2 w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
          />
        )}
        <p className="text-[10px] text-gray-600 mt-1">仅作用于界面文字，不影响源码展示</p>
      </div>

      {/* 大纲跳转高亮时长 */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500">大纲跳转高亮时长</label>
          <span className="text-xs text-gray-600">{(config.highlight_duration / 1000).toFixed(1)}s</span>
        </div>
        <input
          type="range"
          min={500}
          max={10000}
          step={500}
          value={config.highlight_duration}
          onChange={e => onChange({ ...config, highlight_duration: parseInt(e.target.value) })}
          className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
        />
        <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
          <span>0.5s</span>
          <span>默认 3s</span>
          <span>10s</span>
        </div>
      </div>

      {/* PDF 缩放参数（折叠） */}
      <div>
        <button
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-400 transition-colors"
        >
          <svg
            className={`w-3 h-3 transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          PDF 缩放参数
        </button>

        {showAdvanced && (
          <div className="mt-3 space-y-3 pl-2 border-l border-gray-800">
            {/* Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">每次缩放步进</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={5}
                max={50}
                step={5}
                value={Math.round(config.zoom_step * 100)}
                onChange={e => onChange({ ...config, zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Zoom Min */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">最小缩放倍率</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_min * 100)}%</span>
              </div>
              <input
                type="range"
                min={10}
                max={100}
                step={10}
                value={Math.round(config.zoom_min * 100)}
                onChange={e => onChange({ ...config, zoom_min: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Zoom Max */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">最大缩放倍率</label>
                <span className="text-xs text-gray-600">{Math.round(config.zoom_max * 100)}%</span>
              </div>
              <input
                type="range"
                min={100}
                max={500}
                step={50}
                value={Math.round(config.zoom_max * 100)}
                onChange={e => onChange({ ...config, zoom_max: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
            </div>

            {/* Pinch Zoom Step */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-gray-500">触控板捏合灵敏度</label>
                <span className="text-xs text-gray-600">{Math.round(config.pinch_zoom_step * 100)}%</span>
              </div>
              <input
                type="range"
                min={1}
                max={10}
                step={1}
                value={Math.round(config.pinch_zoom_step * 100)}
                onChange={e => onChange({ ...config, pinch_zoom_step: parseInt(e.target.value) / 100 })}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
                <span>低 (1%)</span>
                <span>默认 2%</span>
                <span>高 (10%)</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
