# 持续迭代优化日志

> 自动生成，每次提交后更新。排除深度研究模块和 test_g。

## 当前版本: v1.13.0

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
| 12 | 代码质量 - 后端错误处理统一 | ⏳ 下轮 | — |
| 13 | 代码质量 - 前端错误边界+类型安全 | ⏳ 下轮 | — |

---

## 迭代 1: 性能优化 (v1.13.0) ✅ 完成

### 单元 A: 后端数据库层优化 ✅
- **提交**: 4c0850b
- **变更**:
  - ✅ 统一引擎单例 — 消除 6 处重复 `_get_engine()` 模式
  - ✅ 添加 2 个复合索引 `(source, created_at)` 和 `(source, category, year)`
  - ✅ 修复 workspaces N+1 查询（GROUP BY）
  - ✅ `list_papers()` 排除 `full_text` 大字段（~300KB/行）

### 单元 B: 后端阻塞 IO + 分析缓存优化 ✅
- **提交**: cce53a9
- **变更**:
  - ✅ `upload_paper()` 文件写入包装 `asyncio.to_thread()`
  - ✅ `code.py` 3 个端点包装 `asyncio.to_thread()`
  - ✅ AnalysisJob 事件列表上限 200 条
  - ✅ 缓存写入每 3 字段一次 + 完成时最终写入
  - ✅ 静默异常改为 `logger.debug()`

### 单元 C: 前端懒加载 + PdfViewer 虚拟化 ✅
- **提交**: 441ca05
- **变更**:
  - ✅ PdfViewer 虚拟化 — 只渲染视口 ±2 页，IntersectionObserver 驱动
  - ✅ ExperimentsTab/CodeTab/InterviewTab 改为 React.lazy
  - ✅ FlowChart mermaid 改为动态导入
  - ✅ Sidebar 限制 30 条

### 单元 D: 前端渲染优化 ✅
- **提交**: a9d6d4b
- **变更**:
  - ✅ GraphCanvas 移除 300ms setInterval 轮询
  - ✅ useUIConfig 拆分为 useThemeConfig/useViewerConfig
  - ✅ CodeViewer 大文件（>2000 行）行号虚拟化
  - ✅ AnalysisPanel/Sidebar 提取 14 个模块级稳定 style 对象

### 验证结果
- ✅ TypeScript 编译零错误
- ✅ Python AST 解析全部通过
- ✅ 前端构建成功

---

## 迭代 2: 代码质量 (v1.14.0) ⏳ 待开始

### 待修复问题
| 编号 | 问题 | 优先级 |
|------|------|--------|
| Q-01 | 后端 60+ 处静默吞错 | 🔴 P0 |
| Q-02 | 后端错误响应格式不一致 | 🟠 P1 |
| Q-03 | 前端缺少路由级 ErrorBoundary | 🟠 P1 |
| Q-04 | 前端 40+ 处 catch (e: any) | 🟠 P1 |
| Q-05 | 加载状态模式不一致 | 🟡 P2 |
| Q-06 | TestButton 组件重复 | 🟡 P2 |
| Q-07 | confirm() 未用 ConfirmDialog | 🟡 P2 |
| Q-08 | SettingsPage 巨型组件拆分 | 🟡 P2 |

---

## 提交历史

| 版本 | 提交信息 | 日期 |
|------|---------|------|
| v1.13.0 | 性能优化：后端数据库层 + 阻塞IO + 前端懒加载 + 渲染优化 | 2026-06-09 |

---

*最后更新: 2026-06-09 迭代 1 完成*
