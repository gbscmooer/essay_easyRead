/**
 * InterviewTab 组件
 *
 * 面试 Tab：以翻转卡片形式展示面试问答。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   - 问答卡片网格布局
 *   - 每张卡片：
 *     - 正面：面试问题（Q）
 *     - 背面：参考回答（A）
 *     - 点击翻转（CSS 3D transform）
 *   - 顶部统计：共 X 道题
 *
 * 交互：
 *   - 点击卡片翻转
 *   - 翻转动画：rotateY(180deg), 0.6s
 *   - 鼠标悬停时卡片微微上浮
 *
 * 样式：
 *   - 网格：grid-cols-1 md:grid-cols-2, gap-4
 *   - 卡片：perspective 容器, min-h-48（最小高度，允许内容撑开）
 *   - 正面：bg-gray-800, 居中文字
 *   - 背面：bg-blue-900/30, 左对齐文字, overflow-auto（长回答可滚动）
 *   - 翻转：transform-style: preserve-3d, backface-visibility: hidden
 */
