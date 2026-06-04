/**
 * PaperList 组件
 *
 * 论文库列表，支持筛选和搜索。
 *
 * Props:
 *   papers: Paper[] - 论文列表
 *   total: number - 总数
 *   onSelect: (id: number) => void - 选中论文
 *   onDelete: (id: number) => void - 删除论文
 *   onSearch: (query: string) => void - 搜索回调
 *   onFilter: (filters: FilterParams) => void - 筛选回调
 *   onPageChange: (page: number) => void - 翻页
 *
 * 筛选参数：
 *   - venue: 会议下拉选择
 *   - year: 年份下拉选择
 *
 * 功能：
 *   - 顶部搜索框 + 筛选下拉
 *   - 论文卡片列表（标题、会议、年份、状态、时间）
 *   - 分页控件
 *   - 空状态提示
 *
 * 样式：
 *   - 搜索栏：sticky top-0, bg-gray-900
 *   - 卡片：bg-gray-800, hover:bg-gray-700, 圆角
 *   - 分页：底部居中
 */
