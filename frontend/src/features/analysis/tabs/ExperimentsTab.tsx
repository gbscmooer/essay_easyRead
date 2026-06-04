/**
 * ExperimentsTab 组件
 *
 * 实验 Tab：展示实验设置和结果对比。
 *
 * Props:
 *   result: AnalysisResult - 解析结果
 *
 * 展示内容：
 *   1. 数据集列表（标签样式）
 *   2. 基线方法列表
 *   3. 主要实验结论（文字描述）
 *   4. 实验结果对比表（基于 result_table 结构化数据）
 *      - result_table: ResultTableRow[] — 每行 {method, dataset, metric, value}
 *      - 使用 Recharts BarChart 可视化
 *      - 支持按 metric 排序
 *      - 高亮本文方法的数据行（method 包含 "Ours" 或 "本文"）
 *      - 如果 result_table 为空，仅展示 main_results 文字
 *   5. 消融实验说明
 *
 * 数据来源：
 *   result.experiments.result_table — AI 从论文中提取的结构化数值数据
 *   每行对应一个方法在某数据集上的某个指标值
 *
 * 依赖：
 *   - recharts (BarChart, Bar, XAxis, YAxis, Tooltip, Legend)
 *
 * 样式：
 *   - 数据集/基线标签：bg-gray-700, rounded-full, px-3, py-1
 *   - 对比表格：带排序功能的表头
 *   - Recharts 图表容器：bg-gray-800, rounded-lg, p-4
 *   - 消融实验：特殊卡片样式
 */
