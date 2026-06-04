/**
 * PdfViewer 组件
 *
 * PDF 文件预览，使用 react-pdf 渲染。
 *
 * Props:
 *   pdfUrl: string - PDF 文件的 URL（如 /api/papers/{id}/pdf 或 blob URL）
 *   currentPage: number - 当前页码（1-based）
 *   onPageChange: (page: number) => void - 翻页回调
 *
 * 功能：
 *   - 渲染当前页的 PDF
 *   - 上一页/下一页按钮
 *   - 页码显示和跳转输入框
 *   - 缩放控制（+/-/重置）
 *   - 自适应容器宽度
 *   - 加载中显示 skeleton
 *
 * 依赖：
 *   - react-pdf (Document, Page, pdfjs)
 *   - 需要配置 pdfjs worker
 *
 * 样式：
 *   - 容器：bg-gray-900, 圆角, 阴影
 *   - 控制栏：底部固定，半透明背景
 *   - 页面：居中显示，阴影
 */
