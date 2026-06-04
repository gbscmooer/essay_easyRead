/**
 * ReaderPage 组件
 *
 * 论文精读主页面，左右分栏布局。
 *
 * 路由：/reader/:id
 *
 * 布局：
 *   - 左侧（40%）：PDF 预览
 *     - Sidebar（可折叠的论文列表）
 *     - PdfViewer
 *   - 右侧（60%）：解析结果
 *     - AnalysisPanel（含 Tab 切换）
 *
 * 功能：
 *   - 从 URL 参数获取 paperId
 *   - 加载论文数据和解析结果
 *   - 如果解析中，显示 LoadingState
 *   - 如果解析完成，显示 AnalysisPanel
 *   - 左侧可切换到其他论文
 *   - 支持拖拽调整左右比例（可选）
 *
 * 样式：
 *   - 容器：flex, h-screen (减去导航栏高度)
 *   - 左侧面板：w-2/5, border-r, border-gray-800
 *   - 右侧面板：w-3/5, overflow-auto
 *   - 响应式：移动端上下布局
 */
