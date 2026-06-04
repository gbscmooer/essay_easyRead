/**
 * FlowChart 组件
 *
 * Mermaid 流程图渲染。
 *
 * Props:
 *   chart: string - Mermaid 语法字符串
 *
 * 功能：
 *   - 使用 mermaid 库渲染流程图
 *   - 深色主题适配（mermaid theme: dark）
 *   - 渲染为 SVG，嵌入页面
 *   - 错误处理：如果语法错误，显示原始文本
 *
 * 依赖：
 *   - mermaid (npm package)
 *
 * 实现要点：
 *   1. mermaid.initialize({ theme: 'dark', ... })
 *   2. mermaid.render(id, chart) 获取 SVG
 *   3. 使用 dangerouslySetInnerHTML 插入 SVG
 *   4. useEffect 在 chart 变化时重新渲染
 *
 * 样式：
 *   - 容器：bg-gray-800, rounded-lg, p-4, overflow-auto
 *   - SVG 居中显示
 */
