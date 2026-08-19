import React, { useState, useCallback, useMemo, useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { SelectionPopup } from '@/components/SelectionPopup'
import { AnnotationLayer } from './AnnotationLayer'
import { useUIConfig } from '@/hooks/useConfig'
import { listAnnotations, createAnnotation, deleteAnnotation, deleteAnnotationGroup } from '@/api/annotations'
import { toast } from '@/hooks/useToast'
import type { Annotation, AnnotationType } from '@/types'
import {
  loadZoom,
  saveZoom,
  loadReadingPage,
  saveReadingPage,
  type FitMode,
  type ZoomPref,
} from '@/utils/readerPrefs'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'


pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()


/** 读回存档缩放时的宽松边界；真正的上下界按运行时 uiConfig 再夹一次。 */
const ZOOM_FLOOR = 0.2
const ZOOM_CEIL = 5

const ANNOTATION_TYPE_LABEL: Record<AnnotationType, string> = {
  highlight: '高亮',
  underline: '下划线',
  strikethrough: '删除线',
}

/** 标注索引里的一条 —— 同一次选中产生的多个矩形合并成一条。 */
interface AnnotationGroup {
  key: string
  page: number
  y: number
  color: string
  type: AnnotationType
  text: string
  ids: number[]
}


interface HeadingPos {
  page: number
  y_percent: number
  text: string
}

interface PdfViewerProps {
  pdfUrl: string
  paperId?: number
  activePage?: number | null
  headings?: HeadingPos[]
  onPageChange: (page: number) => void
}

export interface PdfViewerHandle {
  jumpToPage: (page: number) => void
  jumpToSection: (page: number, searchText: string) => void
}

export const PdfViewer = forwardRef<PdfViewerHandle, PdfViewerProps>(function PdfViewer(
  { pdfUrl, paperId, activePage, headings, onPageChange },
  ref,
) {
  const uiConfig = useUIConfig()
  const [numPages, setNumPages] = useState(0)
  // 缩放与适配模式是同一个偏好的两半，放在一个 state 里，存档时不会互相错拍
  const [zoomPref, setZoomPref] = useState<ZoomPref>(
    () => loadZoom({ scale: 1, fitMode: 'none' }, ZOOM_FLOOR, ZOOM_CEIL),
  )
  const { scale, fitMode } = zoomPref
  const [pageInput, setPageInput] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [visiblePage, setVisiblePage] = useState(1)
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [showAnnotationIndex, setShowAnnotationIndex] = useState(false)
  const [visibleRange, setVisibleRange] = useState({ start: 1, end: 3 })
  const [pageMetricsReady, setPageMetricsReady] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isScrollingRef = useRef(false)
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const sectionHighlightTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const visiblePageRef = useRef(visiblePage)
  const pageSizeRef = useRef<{ width: number; height: number } | null>(null)
  const restoredForRef = useRef<number | null>(null)
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())

  /** 统一登记的延时器：卸载或换论文时一次性清干净，不留悬空回调。 */
  const schedule = useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timersRef.current.delete(id)
      fn()
    }, ms)
    timersRef.current.add(id)
    return id
  }, [])

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      timers.forEach(clearTimeout)
      timers.clear()
    }
  }, [])

  // Load annotations when paperId changes
  useEffect(() => {
    if (!paperId) {
      setAnnotations([])
      return
    }
    listAnnotations(paperId)
      .then(setAnnotations)
      .catch(() => setAnnotations([]))
  }, [paperId])

  const handleAnnotate = useCallback(async (data: {
    pageNumber: number
    x: number
    y: number
    width: number
    height: number
    annotationType: AnnotationType
    color: string
    text: string
    groupId: string
  }) => {
    if (!paperId) return
    try {
      const ann = await createAnnotation(paperId, {
        page_number: data.pageNumber,
        x: data.x,
        y: data.y,
        width: data.width,
        height: data.height,
        annotation_type: data.annotationType,
        color: data.color,
        text: data.text,
        group_id: data.groupId,
      })
      setAnnotations(prev => [...prev, ann])
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '创建标注失败' })
    }
  }, [paperId])

  const handleDeleteAnnotation = useCallback(async (id: number) => {
    try {
      // Find the annotation to check its group_id
      const target = annotations.find(a => a.id === id)
      if (target?.group_id) {
        // Delete the entire group
        await deleteAnnotationGroup(target.group_id)
        const gid = target.group_id
        setAnnotations(prev => prev.filter(a => a.group_id !== gid))
      } else {
        // Fallback: single delete (legacy annotations without group_id)
        await deleteAnnotation(id)
        setAnnotations(prev => prev.filter(a => a.id !== id))
      }
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '删除标注失败' })
    }
  }, [annotations])

  const onDocumentLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total)
    setIsLoading(false)
    setLoadError(null)
  }, [])

  /**
   * 加载失败必须落到界面上。此前这里只把 isLoading 置 false，用户得到的是
   * 一片空白的滚动区 —— 既不知道出了事，也没有任何补救入口。
   */
  const onDocumentLoadError = useCallback((error: Error) => {
    setIsLoading(false)
    setLoadError(error?.message || '无法读取该 PDF 文件')
  }, [])

  const retryLoad = useCallback(() => {
    setLoadError(null)
    setIsLoading(true)
    setReloadToken(token => token + 1)
  }, [])

  /** 首页渲染完成时记下页面原始尺寸，供「适应宽度 / 适应页面」反解缩放比。 */
  const onPageRenderSuccess = useCallback((page: { getViewport?: (p: { scale: number }) => { width: number; height: number } }) => {
    if (pageSizeRef.current) return
    try {
      const viewport = page.getViewport?.({ scale: 1 })
      if (viewport && viewport.width > 0 && viewport.height > 0) {
        pageSizeRef.current = { width: viewport.width, height: viewport.height }
        setPageMetricsReady(true)
      }
    } catch {
      // 拿不到 viewport 时保持手动缩放即可
    }
  }, [])

  const scrollToPage = useCallback((page: number) => {
    const container = scrollRef.current
    const el = pageRefs.current.get(page)
    if (!container || !el) return

    isScrollingRef.current = true
    if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)

    const containerRect = container.getBoundingClientRect()
    const elRect = el.getBoundingClientRect()
    const offset = elRect.top - containerRect.top + container.scrollTop - 16

    container.scrollTo({ top: offset, behavior: 'smooth' })

    scrollTimerRef.current = setTimeout(() => {
      isScrollingRef.current = false
      setVisiblePage(page)
      onPageChange(page)
    }, 800)
  }, [onPageChange])

  /**
   * 让目标页进入虚拟化窗口并等它真的挂载后再滚动。
   *
   * 之前所有跳转都直接调 scrollToPage，而窗口外的页在 pageRefs 里没有节点，
   * 函数会静默 return —— 「输入页码跳转」「大纲跳远处章节」于是时灵时不灵。
   */
  const ensurePageRendered = useCallback((page: number, done: () => void) => {
    setVisibleRange(prev => (page < prev.start || page > prev.end
      ? { start: Math.max(1, page - 2), end: Math.min(numPages, page + 2) }
      : prev))

    let attempts = 0
    const tick = () => {
      if (pageRefs.current.get(page)) {
        done()
        return
      }
      if (attempts++ < 25) schedule(tick, 80)
    }
    schedule(tick, 0)
  }, [numPages, schedule])

  useImperativeHandle(ref, () => ({
    jumpToPage: (page: number) => {
      if (page >= 1 && page <= numPages) {
        ensurePageRendered(page, () => scrollToPage(page))
      }
    },
    jumpToSection: (page: number, searchText: string) => {
      if (page < 1 || page > numPages) return

      // 先跳到目标页（窗口外的页先渲染出来再滚）
      ensurePageRendered(page, () => scrollToPage(page))

      // 策略1: 使用后端提取的标题坐标精确定位
      const pageHeadings = headings?.filter(h => h.page === page) || []
      if (pageHeadings.length > 0) {
        // 按文字匹配找到最佳标题，回退到该页第一个标题
        const normalizedSearch = searchText ? searchText.toLowerCase().trim() : ''
        let targetHeading = pageHeadings[0]
        if (normalizedSearch) {
          const match = pageHeadings.find(h => {
            const ht = h.text.toLowerCase().trim()
            return ht === normalizedSearch || ht.includes(normalizedSearch) || normalizedSearch.includes(ht)
          })
          if (match) targetHeading = match
        }
        const scrollToHeading = () => {
          const pageEl = pageRefs.current.get(page)
          if (!pageEl) return
          const container = scrollRef.current
          if (!container) return
          const pageHeight = pageEl.offsetHeight
          const targetY = targetHeading.y_percent * pageHeight
          const containerRect = container.getBoundingClientRect()
          const pageRect = pageEl.getBoundingClientRect()
          const offset = pageRect.top - containerRect.top + container.scrollTop + targetY - 120
          container.scrollTo({ top: offset, behavior: 'smooth' })
        }
        // 等待页面渲染
        setTimeout(scrollToHeading, 500)
        return
      }

      // 策略2: 回退到 TextLayer 文字匹配
      const tryFind = (attempts: number) => {
        const pageEl = pageRefs.current.get(page)
        if (!pageEl) return
        const textLayer = pageEl.querySelector('.react-pdf__Page__textContent')
        if (!textLayer) {
          if (attempts < 15) setTimeout(() => tryFind(attempts + 1), 200)
          return
        }
        if (!searchText) return

        const spans = Array.from(textLayer.querySelectorAll('span'))
        const normalizedSearch = searchText.toLowerCase().trim()
        let matchSpan = spans.find(s => (s.textContent || '').toLowerCase().trim() === normalizedSearch)
        if (!matchSpan) {
          matchSpan = spans.find(s => {
            const text = (s.textContent || '').toLowerCase().trim()
            return text.includes(normalizedSearch) || normalizedSearch.includes(text)
          })
        }
        if (!matchSpan) {
          const firstWord = normalizedSearch.split(/\s+/)[0]
          if (firstWord && firstWord.length > 2) {
            matchSpan = spans.find(s => (s.textContent || '').toLowerCase().trim().startsWith(firstWord))
          }
        }

        if (matchSpan) {
          const container = scrollRef.current
          if (container) {
            const containerRect = container.getBoundingClientRect()
            const spanRect = matchSpan.getBoundingClientRect()
            const offset = spanRect.top - containerRect.top + container.scrollTop - 120
            container.scrollTo({ top: offset, behavior: 'smooth' })
          }
          // 高亮
          if (sectionHighlightTimerRef.current) clearTimeout(sectionHighlightTimerRef.current)
          textLayer.querySelectorAll('.section-highlight').forEach(el => {
            const parent = el.parentNode
            if (parent) { parent.replaceChild(document.createTextNode(el.textContent || ''), el); parent.normalize() }
          })
          const mark = document.createElement('mark')
          mark.className = 'section-highlight'
          mark.style.cssText = 'background: rgba(250, 204, 21, 0.4); color: inherit; border-radius: 2px; padding: 1px 0; transition: opacity 0.5s ease;'
          matchSpan.style.position = 'relative'
          matchSpan.insertBefore(mark, matchSpan.firstChild)
          while (mark.nextSibling) mark.appendChild(mark.nextSibling)
          sectionHighlightTimerRef.current = setTimeout(() => {
            mark.style.opacity = '0'
            setTimeout(() => {
              const parent = mark.parentNode
              if (parent) { while (mark.firstChild) parent.insertBefore(mark.firstChild, mark); parent.removeChild(mark); parent.normalize() }
            }, 500)
          }, 5000)
        }
      }
      setTimeout(() => tryFind(0), 600)
    },
  }), [numPages, scrollToPage, ensurePageRendered, headings])

  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    let scrollTimer: ReturnType<typeof setTimeout>
    const handleScroll = () => {
      isScrollingRef.current = true
      clearTimeout(scrollTimer)
      scrollTimer = setTimeout(() => {
        isScrollingRef.current = false
      }, 150)
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      container.removeEventListener('scroll', handleScroll)
      clearTimeout(scrollTimer)
    }
  }, [])

  useEffect(() => { visiblePageRef.current = visiblePage }, [visiblePage])

  /**
   * IntersectionObserver 跟踪当前可见页 + 推进虚拟化窗口。
   *
   * 依赖必须包含窗口边界：observer 只在建立时 observe 了当时已挂载的页，
   * 而虚拟化后来新渲染的页不会被自动纳入。此前依赖只有 numPages，于是滚过
   * 最初那几页之后就再没有 entry 上报，窗口停止推进 —— 长论文往下翻会翻到空白。
   */
  useEffect(() => {
    const container = scrollRef.current
    if (!container || numPages === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        // 更新可见页面范围（始终执行，不依赖 isScrolling）
        const visiblePages: number[] = []
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pn = Number(entry.target.getAttribute('data-page'))
            if (pn) visiblePages.push(pn)
          }
        }
        if (visiblePages.length > 0) {
          const minVis = Math.min(...visiblePages)
          const maxVis = Math.max(...visiblePages)
          const buffer = 2
          const start = Math.max(1, minVis - buffer)
          const end = Math.min(numPages, maxVis + buffer)
          // 值没变就复用上一份对象，避免滚动时每个 entry 都触发一次重渲染
          setVisibleRange(prev => (prev.start === start && prev.end === end ? prev : { start, end }))
        }

        // 更新当前最可见页（仅非滚动时）
        if (isScrollingRef.current) return

        let maxRatio = 0
        let mostVisible = 1
        for (const entry of entries) {
          if (entry.intersectionRatio > maxRatio) {
            maxRatio = entry.intersectionRatio
            const pageNum = Number(entry.target.getAttribute('data-page'))
            if (pageNum) mostVisible = pageNum
          }
        }
        if (maxRatio > 0 && mostVisible !== visiblePageRef.current) {
          setVisiblePage(mostVisible)
        }
      },
      {
        root: container,
        threshold: [0, 0.25, 0.5, 0.75, 1],
      }
    )

    pageRefs.current.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [numPages, visibleRange.start, visibleRange.end])


  // 当页面跳转时，扩展可见范围确保目标页被渲染
  useEffect(() => {
    setVisibleRange(prev => {
      if (activePage && activePage >= 1 && activePage <= numPages) {
        if (activePage < prev.start || activePage > prev.end) {
          return {
            start: Math.max(1, activePage - 2),
            end: Math.min(numPages, activePage + 2),
          }
        }
      }
      return prev
    })
  }, [activePage, numPages])

  const clampScale = useCallback((value: number) => {
    const min = uiConfig.zoom_min || 0.4
    const max = uiConfig.zoom_max || 3
    return Math.min(max, Math.max(min, value))
  }, [uiConfig])

  /** 任何手动缩放都会退出适配模式 —— 用户接管后就不该再被自动改回去。 */
  const applyScale = useCallback((next: number | ((current: number) => number), mode: FitMode = 'none') => {
    setZoomPref(prev => {
      const raw = typeof next === 'function' ? next(prev.scale) : next
      return { scale: clampScale(raw), fitMode: mode }
    })
  }, [clampScale])

  // 缩放偏好跨论文保留（视力与屏幕是人的属性，不是某一篇 PDF 的属性）
  useEffect(() => { saveZoom(zoomPref) }, [zoomPref])

  /** 按容器可用空间反解「适应宽度 / 适应页面」的缩放比。 */
  const computeFitScale = useCallback((mode: FitMode): number | null => {
    const container = scrollRef.current
    const size = pageSizeRef.current
    if (!container || !size || mode === 'none') return null
    const usableWidth = container.clientWidth - 56   // 两侧留白 + 左侧页码栏
    const usableHeight = container.clientHeight - 48
    if (usableWidth <= 0 || usableHeight <= 0) return null
    const widthScale = usableWidth / size.width
    const raw = mode === 'width' ? widthScale : Math.min(widthScale, usableHeight / size.height)
    return clampScale(raw)
  }, [clampScale])

  // 适配模式下随容器尺寸重算（拖分割条、折叠面板、改窗口都会触发）
  useEffect(() => {
    if (fitMode === 'none') return
    const container = scrollRef.current
    if (!container) return

    const apply = () => {
      const next = computeFitScale(fitMode)
      if (next == null) return
      setZoomPref(prev => (Math.abs(prev.scale - next) < 0.005 ? prev : { ...prev, scale: next }))
    }
    apply()

    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(apply)
    observer.observe(container)
    return () => observer.disconnect()
  }, [fitMode, computeFitScale, pageMetricsReady])

  const toggleFit = useCallback((mode: Exclude<FitMode, 'none'>) => {
    setZoomPref(prev => ({ ...prev, fitMode: prev.fitMode === mode ? 'none' : mode }))
  }, [])

  // 两指捏合缩放（触控板 pinch 触发 ctrlKey+wheel）
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    const handleWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      const delta = -e.deltaY
      if (delta === 0) return
      const step = uiConfig.pinch_zoom_step || 0.02
      applyScale(current => current + (delta > 0 ? step : -step))
    }

    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [uiConfig, applyScale])

  useEffect(() => {
    return () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)
      if (sectionHighlightTimerRef.current) clearTimeout(sectionHighlightTimerRef.current)
    }
  }, [])

  // 换一篇论文：重置加载态与恢复标记。此前 isLoading 只在首次挂载时为 true，
  // 切论文时既不显示骨架屏，也会把上一篇的页码带过去。
  useEffect(() => {
    setIsLoading(true)
    setLoadError(null)
    setNumPages(0)
    setVisiblePage(1)
    setVisibleRange({ start: 1, end: 3 })
    setShowAnnotationIndex(false)
    pageSizeRef.current = null
    setPageMetricsReady(false)
    restoredForRef.current = null
  }, [pdfUrl])

  // 恢复上次读到的页码（只在这篇论文首次加载完成时做一次）
  useEffect(() => {
    if (!paperId || numPages === 0 || restoredForRef.current === paperId) return
    restoredForRef.current = paperId

    const saved = loadReadingPage(paperId)
    if (!saved || saved < 2 || saved > numPages) return

    ensurePageRendered(saved, () => {
      scrollToPage(saved)
      setVisiblePage(saved)
      toast({ type: 'info', message: `已回到上次阅读位置 · 第 ${saved} 页` })
    })
  }, [paperId, numPages, ensurePageRendered, scrollToPage])

  // 记住当前页（防抖，避免连续滚动时高频写 storage）
  useEffect(() => {
    if (!paperId || numPages === 0 || restoredForRef.current !== paperId) return
    const timer = setTimeout(() => saveReadingPage(paperId, visiblePage), 800)
    return () => clearTimeout(timer)
  }, [paperId, visiblePage, numPages])

  const goToPage = useCallback((page: number) => {
    if (page >= 1 && page <= numPages) {
      ensurePageRendered(page, () => scrollToPage(page))
    }
  }, [numPages, ensurePageRendered, scrollToPage])

  const handlePageInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const page = parseInt(pageInput, 10)
      if (!isNaN(page)) goToPage(page)
      setPageInput('')
    }
  }, [pageInput, goToPage])

  const zoomIn = useCallback(() => applyScale(s => s + (uiConfig.zoom_step || 0.2)), [applyScale, uiConfig])
  const zoomOut = useCallback(() => applyScale(s => s - (uiConfig.zoom_step || 0.2)), [applyScale, uiConfig])
  const resetZoom = useCallback(() => applyScale(1.0), [applyScale])

  /**
   * 键盘翻页与缩放。
   *
   * 阅读器没有键盘操作时，读一篇 30 页的论文全靠鼠标滚轮和底部两个按钮。
   * 这里只接管明确属于「文档平面」的按键，右侧分析面板、对话框、Tab 栏和
   * 分割条各有自己的方向键语义，一律让行。
   */
  useEffect(() => {
    if (numPages === 0) return

    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const target = e.target as HTMLElement | null
      if (target) {
        const tag = target.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) return
        if (target.closest('.rdr-panel, [role="dialog"], [role="tablist"], [data-reader-resizer]')) return
      }

      const current = visiblePageRef.current
      switch (e.key) {
        case 'ArrowRight':
        case 'PageDown':
          goToPage(Math.min(numPages, current + 1)); break
        case 'ArrowLeft':
        case 'PageUp':
          goToPage(Math.max(1, current - 1)); break
        case 'Home':
          goToPage(1); break
        case 'End':
          goToPage(numPages); break
        case '+':
        case '=':
          zoomIn(); break
        case '-':
        case '_':
          zoomOut(); break
        case '0':
          resetZoom(); break
        default:
          return
      }
      e.preventDefault()
    }

    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [numPages, goToPage, zoomIn, zoomOut, resetZoom])

  /**
   * 标注索引：同一次选中产生的多个矩形按 group_id 合成一条，
   * 按页码 + 纵向位置排序 —— 标注做完之后要能回得去。
   */
  const annotationGroups = useMemo<AnnotationGroup[]>(() => {
    const map = new Map<string, AnnotationGroup>()
    for (const ann of annotations) {
      const key = ann.group_id ?? `single-${ann.id}`
      const existing = map.get(key)
      if (!existing) {
        map.set(key, {
          key,
          page: ann.page_number,
          y: ann.y,
          color: ann.color,
          type: ann.annotation_type,
          text: ann.text || '',
          ids: [ann.id],
        })
        continue
      }
      existing.ids.push(ann.id)
      if (ann.page_number < existing.page || (ann.page_number === existing.page && ann.y < existing.y)) {
        existing.page = ann.page_number
        existing.y = ann.y
      }
    }
    return Array.from(map.values()).sort((a, b) => a.page - b.page || a.y - b.y)
  }, [annotations])

  const jumpToAnnotation = useCallback((group: AnnotationGroup) => {
    ensurePageRendered(group.page, () => {
      scrollToPage(group.page)
      schedule(() => {
        const container = scrollRef.current
        const pageEl = pageRefs.current.get(group.page)
        if (!container || !pageEl) return
        const offset = pageEl.getBoundingClientRect().top
          - container.getBoundingClientRect().top
          + container.scrollTop
          + group.y * pageEl.offsetHeight
          - 120
        container.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' })
      }, 450)
    })
  }, [ensurePageRendered, scrollToPage, schedule])

  // 控制栏按钮统一交互：hover 抬升表面、disabled 钝化、focus 可见
  const ctrlBtnClass =
    'inline-flex items-center justify-center h-7 px-2.5 text-sm rounded-lg press focus-glow transition-colors duration-150 disabled:opacity-30 disabled:cursor-not-allowed disabled:pointer-events-none'
  const ctrlBtnStyle: React.CSSProperties = {
    color: 'var(--text-secondary)',
    backgroundColor: 'var(--bg-elevated)',
    transitionTimingFunction: 'var(--ease-press)',
  }
  const onCtrlEnter = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    if (!e.currentTarget.disabled) e.currentTarget.style.backgroundColor = 'var(--bg-surface)'
  }, [])
  const onCtrlLeave = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.backgroundColor = 'var(--bg-elevated)'
  }, [])

  return (
    <div className="flex flex-col h-full overflow-hidden relative" style={{ backgroundColor: 'var(--bg-surface)' }}>
      {/* 文本选择弹窗 */}
      <SelectionPopup containerRef={scrollRef} onAnnotate={handleAnnotate} />

      {/* PDF 内容区 — 连续滚动 */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {isLoading && !loadError && (
          <div className="w-full h-full flex flex-col items-center justify-center gap-4 py-8 animate-reveal-fade">
            {/* 页面骨架占位 — 模拟一张正在加载的论文页 */}
            <div
              className="skeleton w-[min(80%,520px)] aspect-[1/1.32] flex flex-col gap-3 p-8"
              style={{ borderRadius: 'var(--seed-radius, 12px)' }}
              aria-hidden="true"
            >
              <div className="skeleton-pulse h-5 w-3/5" />
              <div className="skeleton-pulse h-3 w-2/5" />
              <div className="mt-4 flex flex-col gap-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="skeleton-pulse h-2.5" style={{ width: `${88 - (i % 3) * 12}%` }} />
                ))}
              </div>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }} role="status">正在加载文档…</span>
          </div>
        )}
        {loadError ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-5 p-8 text-center" role="alert">
            <div
              className="w-12 h-12 flex items-center justify-center"
              style={{ border: '1px solid var(--border)', color: 'var(--error)' }}
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              </svg>
            </div>
            <div className="max-w-[26rem]">
              <p className="text-[15px] mb-1.5" style={{ color: 'var(--text-primary)' }}>PDF 加载失败</p>
              <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                {loadError}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={retryLoad}
                className={ctrlBtnClass}
                style={ctrlBtnStyle}
                onMouseEnter={onCtrlEnter}
                onMouseLeave={onCtrlLeave}
              >
                重新加载
              </button>
              <a
                href={pdfUrl}
                target="_blank"
                rel="noreferrer"
                className={ctrlBtnClass}
                style={ctrlBtnStyle}
              >
                在新标签页打开
              </a>
            </div>
          </div>
        ) : (
        <Document
          key={reloadToken}
          file={pdfUrl}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={null}
        >
          <div className="flex flex-col items-center gap-4 py-4">
            {Array.from({ length: numPages }, (_, i) => i + 1)
              .filter(pageNum => pageNum >= visibleRange.start && pageNum <= visibleRange.end)
              .map(pageNum => (
              <div
                key={pageNum}
                data-page={pageNum}
                ref={el => {
                  if (el) pageRefs.current.set(pageNum, el)
                  else pageRefs.current.delete(pageNum)
                }}
                className="relative transition-all duration-300"
                style={activePage === pageNum
                  ? { boxShadow: '0 0 0 2px var(--accent)', transform: 'scale(1.01)' }
                  : undefined}
              >
                <Page
                  pageNumber={pageNum}
                  scale={scale}
                  onLoadSuccess={onPageRenderSuccess}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                  className=""
                />
                {/* 标注叠加层 */}
                <AnnotationLayer
                  annotations={annotations}
                  pageNumber={pageNum}
                  onDelete={handleDeleteAnnotation}
                />
                {/* 页码标注 */}
                <div className="absolute -left-12 top-0 text-xs w-8 text-right select-none" style={{ color: 'var(--text-muted)' }}>
                  {pageNum}
                </div>
                {/* 跳转高亮光晕 */}
                {activePage === pageNum && (
                  <div className="absolute inset-0 pointer-events-none" style={{ backgroundColor: 'var(--accent-subtle)' }} />
                )}
              </div>
            ))}
          </div>
        </Document>
        )}
      </div>

      {/* 标注索引面板 */}
      {showAnnotationIndex && annotationGroups.length > 0 && (
        <aside
          className="absolute right-3 bottom-14 z-20 w-[19rem] max-h-[55%] flex flex-col overflow-hidden"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            boxShadow: '0 12px 32px rgba(0,0,0,0.28)',
          }}
          aria-label="标注索引"
        >
          <header
            className="flex items-center justify-between px-3 py-2 shrink-0"
            style={{ borderBottom: '1px solid var(--border)' }}
          >
            <span className="text-[11px] tracking-wide" style={{ color: 'var(--text-muted)' }}>
              MARKS / {String(annotationGroups.length).padStart(2, '0')}
            </span>
            <button
              type="button"
              onClick={() => setShowAnnotationIndex(false)}
              aria-label="关闭标注索引"
              className="focus-glow press p-1 text-sm leading-none"
              style={{ color: 'var(--text-muted)' }}
            >
              ×
            </button>
          </header>

          <div className="overflow-auto">
            {annotationGroups.map(group => (
              <div
                key={group.key}
                className="group flex items-start gap-2 px-3 py-2"
                style={{ borderBottom: '1px solid var(--border-light)' }}
              >
                <button
                  type="button"
                  onClick={() => jumpToAnnotation(group)}
                  className="focus-glow flex-1 min-w-0 text-left flex items-start gap-2"
                  title={group.text || '跳转到标注'}
                >
                  <span
                    className="mt-1 w-2.5 h-2.5 shrink-0"
                    style={{ backgroundColor: group.color }}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className="block text-[12px] leading-snug line-clamp-2"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {group.text.trim() || '（无文字）'}
                    </span>
                    <span className="block text-[10px] mt-0.5 tabular-nums" style={{ color: 'var(--text-muted)' }}>
                      第 {group.page} 页 · {ANNOTATION_TYPE_LABEL[group.type]}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => handleDeleteAnnotation(group.ids[0])}
                  aria-label={`删除第 ${group.page} 页的标注`}
                  title="删除标注"
                  className="focus-glow press shrink-0 p-1 opacity-0 group-hover:opacity-70 hover:!opacity-100 focus-visible:opacity-100 transition-opacity"
                  style={{ color: 'var(--text-muted)' }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </aside>
      )}

      {/* 底部控制栏 */}
      <div className="glass-toolbar flex items-center justify-between px-4 py-2 border-0 border-t" style={{ borderTopColor: 'var(--border)', borderRadius: 0 }}>
        {/* 翻页控制 */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => goToPage(Math.max(1, visiblePage - 1))}
            disabled={visiblePage <= 1}
            className={ctrlBtnClass}
            style={ctrlBtnStyle}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            aria-label="上一页"
          >
            上一页
          </button>

          <div className="flex items-center gap-1 text-sm px-1" style={{ color: 'var(--text-secondary)' }}>
            <input
              type="text"
              value={pageInput}
              onChange={e => setPageInput(e.target.value)}
              onKeyDown={handlePageInputKeyDown}
              placeholder={String(visiblePage)}
              aria-label="跳转到页码"
              className="w-10 h-7 text-center text-sm focus:outline-none focus-glow transition-colors duration-150"
              style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-light)', color: 'var(--text-primary)' }}
            />
            <span className="tabular-nums">/ {numPages || '--'}</span>
          </div>

          <button
            onClick={() => goToPage(Math.min(numPages, visiblePage + 1))}
            disabled={visiblePage >= numPages}
            className={ctrlBtnClass}
            style={ctrlBtnStyle}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            aria-label="下一页"
          >
            下一页
          </button>
        </div>

        {/* 标注索引入口 —— 标注做完之后要能按图索骥回到原处 */}
        {annotationGroups.length > 0 && (
          <button
            type="button"
            onClick={() => setShowAnnotationIndex(v => !v)}
            aria-expanded={showAnnotationIndex}
            className={`${ctrlBtnClass} text-xs tabular-nums`}
            style={{
              ...ctrlBtnStyle,
              ...(showAnnotationIndex ? { color: 'var(--accent)' } : null),
            }}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            title={showAnnotationIndex ? '收起标注索引' : '展开标注索引'}
          >
            {annotationGroups.length} 处标注
          </button>
        )}

        {/* 缩放控制 */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => toggleFit('width')}
            aria-pressed={fitMode === 'width'}
            className={`${ctrlBtnClass} text-xs`}
            style={{ ...ctrlBtnStyle, ...(fitMode === 'width' ? { color: 'var(--accent)' } : null) }}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            title="适应宽度（随面板宽度自动重算）"
          >
            适宽
          </button>
          <button
            onClick={() => toggleFit('page')}
            aria-pressed={fitMode === 'page'}
            className={`${ctrlBtnClass} text-xs`}
            style={{ ...ctrlBtnStyle, ...(fitMode === 'page' ? { color: 'var(--accent)' } : null) }}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            title="适应整页"
          >
            适页
          </button>
          <button
            onClick={zoomOut}
            className={`${ctrlBtnClass} w-7 px-0 text-base leading-none`}
            style={ctrlBtnStyle}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            aria-label="缩小"
          >
            −
          </button>
          <button
            onClick={resetZoom}
            className={`${ctrlBtnClass} min-w-[3rem] text-xs tabular-nums`}
            style={ctrlBtnStyle}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            aria-label="重置缩放（0）"
            title="重置缩放（0）"
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            onClick={zoomIn}
            className={`${ctrlBtnClass} w-7 px-0 text-base leading-none`}
            style={ctrlBtnStyle}
            onMouseEnter={onCtrlEnter}
            onMouseLeave={onCtrlLeave}
            aria-label="放大"
          >
            +
          </button>
        </div>
      </div>
    </div>
  )
})
