# 持续迭代优化日志

> 自动生成，每次提交后更新。排除深度研究模块和 test_g。

## 当前版本: v1.15.0

---

## 总体进度

| #  | 工作单元 | 状态 | 合并/提交 |
|----|---------|------|-----------|
| 1  | ConfirmDialog 组件 | ✅ | d62f564 |
| 2  | 静默吞错修复 | ✅ | 4b65b2d |
| 3  | Pages a11y + 加载状态 | ✅ | 377b811 |
| 4  | ARIA + 焦点管理 | ✅ | f4d88a8 |
| 5  | CodePage 响应式 | ✅ | 已含于其他合并 |
| 6  | 主题一致性 | ✅ | a469cad |
| 7  | 视觉一致性 | ✅ | e4991c4 |
| 8  | 性能优化 - 后端数据库层 | ✅ | 4c0850b |
| 9  | 性能优化 - 后端阻塞IO | ✅ | cce53a9 |
| 10 | 性能优化 - 前端懒加载+虚拟化 | ✅ | 441ca05 |
| 11 | 性能优化 - 前端渲染优化 | ✅ | a9d6d4b |
| 12 | 代码质量 - 后端错误处理标准化 | ✅ | cdd2e35 |
| 13 | 代码质量 - 前端ErrorBoundary+类型安全 | ✅ | f49602c |
| 14 | 代码质量 - 前端一致性修复 | ✅ | f49602c |
| 15 | 深度优化 - 后端类型化返回+tags N+1 | ✅ | fb0003c |
| 16 | 深度优化 - SettingsPage组件拆分 | ✅ | 0863b7d |
| 17 | 深度优化 - 前端bundle+CSS优化 | ✅ | 0bd9620 |

---

## 迭代 1: 性能优化 (v1.13.0) ✅ 完成

- **单元 A**: 统一引擎单例、添加复合索引、消除N+1查询、排除full_text大字段
- **单元 B**: asyncio.to_thread包装、事件列表上限、缓存写入频率优化
- **单元 C**: PdfViewer虚拟化、Tab懒加载、FlowChart动态导入、Sidebar分页
- **单元 D**: 移除GraphCanvas轮询、拆分useConfig hooks、CodeViewer虚拟化、style对象优化

---

## 迭代 2: 代码质量 (v1.14.0) ✅ 完成

- **单元 E**: 消除所有静默异常吞没、统一API错误响应格式、补充返回类型注解
- **单元 F**: 创建PageErrorBoundary、8个路由包裹ErrorBoundary、14处catch类型安全
- **单元 G**: 创建LoadingSpinner、替换confirm()为ConfirmDialog、移除重复TestButton

---

## 迭代 3: 深度优化 (v1.15.0) ✅ 完成

### 单元 H: 后端类型化返回 + tags N+1 ✅
- **提交**: fb0003c
- **变更**:
  - ✅ `get_paper()` / `get_analysis()` 返回类型化模型替代 dict
  - ✅ 所有调用者改为属性访问（`paper.field` 替代 `paper["field"]`）
  - ✅ tags 3 处 N+1 查询全部修复为 GROUP BY/JOIN
  - ✅ annotations.group_id 添加索引

### 单元 I: SettingsPage 组件拆分 ✅
- **提交**: 0863b7d
- **变更**:
  - ✅ SettingsPage 从 1306 行缩减至 206 行
  - ✅ 提取 7 个独立组件：config.ts、ProviderPresets、AIModelSection、UIConfigSection、TestButton、DeepResearchSettings
  - ✅ 创建 types/config.ts 统一类型导出
  - ✅ Deep Research 部分使用 Suspense 懒加载

### 单元 J: 前端 bundle + CSS 优化 ✅
- **提交**: 0bd9620
- **变更**:
  - ✅ highlight.js 12 语言按需加载（动态 import）
  - ✅ CodeAnalysisPanel mermaid 动态导入（CodePage -47KB）
  - ✅ CSS 主题覆盖规则精简（~90 → ~40 条）
  - ✅ Vite 构建启用 cssCodeSplit

### 构建体积对比
| Chunk | 优化前 | 优化后 | 变化 |
|-------|--------|--------|------|
| CodePage | 243.24 kB | 195.67 kB | -47.57 kB |
| vendor-mermaid | 340.52 kB | 230.13 kB | -110.39 kB |
| vendor-hljs | (内嵌) | 20.78 kB | 独立核心 |

### 验证结果
- ✅ TypeScript 编译零错误
- ✅ 前端构建成功

---

## 提交历史

| 版本 | 提交信息 | 日期 |
|------|---------|------|
| v1.13.0 | 性能优化：后端数据库层 + 阻塞IO + 前端懒加载 + 渲染优化 | 2026-06-09 |
| v1.14.0 | 代码质量：后端错误处理 + 前端ErrorBoundary + 一致性修复 | 2026-06-09 |
| v1.15.0 | 深度优化：类型化返回 + SettingsPage拆分 + bundle优化 | 2026-06-09 |

---

*最后更新: 2026-06-09 迭代 3 完成*
