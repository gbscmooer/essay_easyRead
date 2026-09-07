import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Maximize2, Minus, Plus, RotateCcw, X } from 'lucide-react'
import '@/styles/exploration-detail.css'

export function ChartView({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [zoom, setZoom] = useState(100)
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    dialog.current?.showModal()
    return () => { dialog.current?.close(); trigger.current?.focus() }
  }, [open])
  return <>
    <div className="ex-chart-head">
      <h3 className="rs-overview__title">{title}</h3>
      <button ref={trigger} type="button" className="ex-icon" title={`放大${title}`} aria-label={`放大${title}`}
        onClick={() => { setZoom(100); setOpen(true) }}><Maximize2 size={18} /></button>
    </div>
    {children}
    {open && createPortal(<dialog ref={dialog} className="ex-chart-dialog" aria-label={`${title}放大视图`}
      onCancel={() => setOpen(false)} onClick={e => { if (e.target === e.currentTarget) setOpen(false) }}>
      <div className="ex-chart-toolbar">
        <h2>{title}</h2>
        <button type="button" className="ex-icon" title="缩小" aria-label="缩小" disabled={zoom <= 75} onClick={() => setZoom(z => z - 25)}><Minus size={18} /></button>
        <output aria-label="缩放比例">{zoom}%</output>
        <button type="button" className="ex-icon" title="放大" aria-label="放大" disabled={zoom >= 250} onClick={() => setZoom(z => z + 25)}><Plus size={18} /></button>
        <button type="button" className="ex-icon" title="重置缩放" aria-label="重置缩放" onClick={() => setZoom(100)}><RotateCcw size={18} /></button>
        <button type="button" className="ex-icon" title="关闭放大视图" aria-label="关闭放大视图" onClick={() => setOpen(false)}><X size={18} /></button>
      </div>
      <div className="ex-chart-scroll"><div className="ex-chart-expanded" style={{ zoom: zoom / 100 }}
        onClick={e => { if ((e.target as Element).closest('button, [role="button"]')) setOpen(false) }}>{children}</div></div>
    </dialog>, document.body)}
  </>
}
