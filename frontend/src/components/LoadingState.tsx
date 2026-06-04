/**
 * LoadingState 组件
 *
 * 解析过程中的加载状态展示。
 *
 * Props:
 *   status: AnalysisStatus - 当前解析状态
 *   message: string - 进度消息
 *   partialResult: Partial<AnalysisResult> - 已解析的部分结果
 *
 * 功能：
 *   - 显示当前阶段的 spinner 动画
 *   - 显示进度消息文字
 *   - 显示解析阶段的步骤指示器：
 *     1. 提取文本 ✓/⟳/○
 *     2. 分析内容 ✓/⟳/○
 *     3. 生成流程图 ✓/⟳/○
 *   - 如果有 partialResult，展示已解析的标题等基本信息
 *
 * 样式：
 *   - 居中展示
 *   - Spinner: border-blue-500 动画
 *   - 步骤指示器：竖向排列，已完成为绿色，进行中为蓝色脉冲
 */
