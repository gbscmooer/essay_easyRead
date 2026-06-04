/**
 * AnalysisPanel 组件
 *
 * 解析结果展示的主面板，包含 Tab 切换。
 *
 * Props:
 *   result: AnalysisResult - 完整解析结果
 *   partialResult: Partial<AnalysisResult> - 流式接收中的部分结果
 *   status: AnalysisStatus - 解析状态
 *   progressMessage: string - 进度消息
 *
 * Tab 结构：
 *   1. 概览 (OverviewTab) - 默认选中
 *   2. 方法 (MethodTab)
 *   3. 实验 (ExperimentsTab)
 *   4. 代码 (CodeTab)
 *   5. 面试 (InterviewTab)
 *
 * 功能：
 *   - 顶部 Tab 栏切换不同视图
 *   - 如果 status 不是 done，显示 LoadingState
 *   - 如果 status 是 done，显示对应 Tab 的内容
 *   - 每个 Tab 独立渲染，切换时不丢失状态
 *
 * 样式：
 *   - Tab 栏：sticky top-0, bg-gray-900, border-b
 *   - Tab 按钮：px-4, py-2, 选中时 text-blue-400, border-b-2, border-blue-400
 *   - 内容区：p-6, overflow-auto
 */
