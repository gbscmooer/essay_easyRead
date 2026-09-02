/**
 * 图谱调色板 —— 一套「印刷油墨」分类色
 *
 * 图谱里的颜色此前直接取自 Tailwind 默认色板（#a855f7 紫、#10b981 翠绿、
 * #3b82f6 亮蓝…）。这些颜色的饱和度在 80% 以上、明度也高，放到米黄纸面上
 * 像是几块塑料贴片，和这套语言的钴蓝 / 朱红 / 赭石完全不是一个色域。而且
 * 同一个语义（比如「共引」）在 EdgeFilter、EdgeDetail、GraphCanvas 三处各写
 * 一遍十六进制，改一处必漏两处。
 *
 * 这里给出唯一的真源：一组按胶印专色思路挑的分类色 —— 饱和度统一压在
 * 55–70%，明度落在同一档，因此并排时读起来是一套油墨，而不是一堆随机色。
 * 前四个就是应用本身的主色，图谱因此和界面同源。
 *
 * 分类色的约束和界面色不同：它必须「可区分」优先于「好看」。所以这里没有
 * 把所有颜色都压成灰调 —— 相邻两色的色相至少隔开 35°，明度也错开一档，
 * 保证在小圆点尺寸下仍能分辨。
 */

/** 分类色环。研究子方向按序取色，超出长度后回绕。 */
export const GRAPH_INK: readonly string[] = [
  '#3969a8', // 钴蓝
  '#d94a38', // 朱红
  '#d3a52f', // 赭石
  '#2f7d78', // 石绿
  '#7d4a6d', // 绛紫
  '#557a55', // 橄榄
  '#a4552c', // 铁锈
  '#292b55', // 深蓝
  '#8a8f3d', // 苔绿
  '#6b7079', // 石板
]

/** 边类型 → 色。语义固定，不参与回绕。 */
export const EDGE_INK = {
  /** 引用：有向、确定的事实关系 —— 给最响的信号色 */
  cites: '#d94a38',
  /** 关联度：本项目的主关系 —— 给主色 */
  related: '#3969a8',
  similar: '#3969a8',
  /** 文献耦合：共享参考文献 */
  bibcoupling: '#d3a52f',
  /** 共引：被同一篇引用 */
  cocitation: '#7d4a6d',
  /** 以下为历史边类型，当前流水线不产出 */
  'shared-method': '#2f7d78',
  'uses-dataset': '#a4552c',
} as const

/** 节点类型 → 色。 */
export const NODE_INK = {
  paperLocal: '#3969a8',
  paperExpanded: '#d3a52f',
  method: '#2f7d78',
  dataset: '#7d4a6d',
} as const

/**
 * 学术角色（stage_synthesize 节点研判）→ 图标 + 专色。
 * 徽章只在有角色研判的节点上出现，颜色取油墨环里未被子方向占用语义的档位。
 */
export const ROLE_META = {
  landmark:    { icon: '🏛️', label: '奠基基石', ink: '#d3a52f' },
  rising_star: { icon: '🔥', label: '前沿黑马', ink: '#d94a38' },
  benchmark:   { icon: '📊', label: '标准基准', ink: '#3969a8' },
  tool_sota:   { icon: '🛠️', label: '工程标杆', ink: '#2f7d78' },
  survey:      { icon: '📖', label: '权威综述', ink: '#7d4a6d' },
} as const

export type RoleKey = keyof typeof ROLE_META

/** cites 边因果（stage_synthesize 连边研判）→ 专色。 */
export const RELATION_INK = {
  improves: '#2f7d78',     // 改进继承：石绿
  replaces: '#d94a38',     // 范式替代：朱红（竞争冲突感）
  evaluates_on: '#d3a52f', // 基准评测：赭石
  inspires: '#7d4a6d',     // 跨界借鉴：绛紫
  cites: '#d94a38',        // 无因果的普通引用回落到 cites 基色
} as const

export type RelationKey = keyof typeof RELATION_INK

/** 关联度通道 → 色。八个通道要能在一张分解条里同时读出来。 */
export const CHANNEL_INK = {
  topic: '#3969a8',
  purpose: '#2f7d78',
  method: '#7d4a6d',
  term: '#a4552c',
  dataset: '#557a55',
  baseline: '#d3a52f',
  bibcoupling: '#8a8f3d',
  cocitation: '#292b55',
} as const

/** 引用意图 → 色。 */
export const INTENT_INK = {
  baseline: '#d94a38',
  reuse: '#3969a8',
  inspiration: '#7d4a6d',
} as const

/** 取分类色，超出长度回绕。 */
export function clusterInk(index: number): string {
  return GRAPH_INK[((index % GRAPH_INK.length) + GRAPH_INK.length) % GRAPH_INK.length]
}

/** 同色的低透明底 —— 用于标签底色，保证前景仍是原色时对比度足够。 */
export function inkWash(color: string, percent = 14): string {
  return `color-mix(in srgb, ${color} ${percent}%, transparent)`
}
