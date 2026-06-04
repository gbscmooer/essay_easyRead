/**
 * MethodTab 组件
 *
 * 方法 Tab：展示方法论详情和流程图。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 方法总览（文字描述）
 *   2. Mermaid 流程图（基于 pipeline 生成）
 *      - 使用 mermaid 库渲染
 *      - 深色主题适配
 *      - 支持缩放和拖拽
 *   3. Pipeline 详细步骤（逐步展示）
 *   4. 关键模块表格
 *      - 表头：模块名 | 作用 | 创新点
 *      - 高亮本文方法的行
 *
 * 依赖：
 *   - mermaid (用于渲染流程图)
 *   - FlowChart 子组件
 *
 * 样式：
 *   - 流程图容器：bg-gray-800, 圆角, p-4, 居中
 *   - 模块表格：table-auto, 条纹背景
 *   - 步骤列表：竖向时间线样式
 */
