/**
 * Sidebar 组件
 *
 * 论文历史列表侧边栏，显示在 ReaderPage 左侧。
 *
 * Props:
 *   papers: Paper[] - 论文列表
 *   activeId: number | null - 当前选中的论文 ID
 *   onSelect: (id: number) => void - 选中论文的回调
 *   onDelete: (id: number) => void - 删除论文的回调
 *
 * 功能：
 *   - 显示论文列表，每项显示标题/文件名、会议、年份
 *   - 高亮当前选中的论文
 *   - 状态标识：done(绿点), analyzing(黄点), failed(红点)
 *   - 点击切换论文
 *   - 右键/按钮删除论文
 *   - 顶部搜索框（本地过滤）
 *   - 底部上传按钮
 *
 * 样式：
 *   - 宽度固定 w-64
 *   - bg-gray-900, border-r, border-gray-800
 *   - 列表项 hover:bg-gray-800, active:bg-gray-700
 */
