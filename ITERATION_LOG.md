# 持续迭代优化日志

> 自动生成，每次提交后更新。排除深度研究模块和 test_g。

## 当前版本: v1.17.0

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
| 18 | 后端code.py优化（缓存+PDF） | ✅ | ce88aa7 |
| 19 | 前端组件优化（memo+防抖） | ✅ | 66e6661 |
| 20 | SmartSearch SQL优化 + 后端清理 | ✅ | 3f0cfb3 |
| 21 | 前端最终清理 | ✅ | 32be2f7 |

---

## 迭代 1: 性能优化 (v1.13.0) ✅

- 统一引擎单例、添加复合索引、消除N+1查询、排除full_text大字段
- asyncio.to_thread包装、事件列表上限、缓存写入频率优化
- PdfViewer虚拟化、Tab懒加载、FlowChart动态导入、Sidebar分页
- 移除GraphCanvas轮询、拆分useConfig hooks、CodeViewer虚拟化、style对象优化

---

## 迭代 2: 代码质量 (v1.14.0) ✅

- 消除所有静默异常吞没、统一API错误响应格式、补充返回类型注解
- 创建PageErrorBoundary、8个路由包裹ErrorBoundary、14处catch类型安全
- 创建LoadingSpinner、替换confirm()为ConfirmDialog、移除重复TestButton

---

## 迭代 3: 深度优化 (v1.15.0) ✅

- `get_paper()`/`get_analysis()` 返回类型化模型、tags N+1 修复、annotations索引
- SettingsPage 1306行→206行、提取7个独立组件、Suspense懒加载
- highlight.js按需加载、mermaid动态导入（CodePage -47KB）、CSS精简（~90→~40条）

---

## 迭代 4: 进一步优化 (v1.16.0) ✅

- code.py 文件树缓存 + PDF 提取优化
- React.memo 列表组件、SSE 批量更新、搜索防抖

---

## 迭代 5: 最终清理 (v1.17.0) ✅

### 单元 M: SmartSearch SQL优化 + 后端清理 ✅
- **提交**: 3f0cfb3
- **变更**:
  - ✅ smart_search LIKE 下推 + JOIN 替代 N+1、仅选必要列
  - ✅ 移除重复引擎单例、补充 import logging、移除过时注释

### 单元 N: 前端最终清理 ✅
- **提交**: 32be2f7
- **变更**:
  - ✅ PaperList confirm() → ConfirmDialog
  - ✅ 13 个文件 catch 类型安全修复（any → unknown）
  - ✅ 确认所有页面使用 LoadingSpinner、无多余 console.log、key props 完整

### 验证结果
- ✅ TypeScript 编译零错误
- ✅ 前端构建成功

---

## 优化成果总结

### 性能优化
| 优化项 | 影响 |
|--------|------|
| 数据库索引 | 列表/搜索查询加速 |
| N+1 查询消除 | tags/workspaces/smart_search 从 N+1 → 1 次查询 |
| 阻塞IO包装 | 事件循环不再阻塞 |
| PdfViewer 虚拟化 | 大 PDF 只渲染视口 ±2 页 |
| Tab 懒加载 | ExperimentsTab/CodeTab/InterviewTab 按需加载 |
| FlowChart/mermaid 动态导入 | CodePage -47KB, vendor-mermaid -110KB |
| highlight.js 按需加载 | 语言包独立 chunk |
| CodeViewer 虚拟化 | >2000 行文件只渲染可见区域 |
| GraphCanvas 轮询移除 | 每 300ms 不再触发重渲染 |
| SSE 批量更新 | 50ms 窗口合并 field 事件 |
| 搜索防抖 | 300ms 延迟避免频繁请求 |
| AnalysisJob 事件上限 | 200 条防内存泄漏 |
| 缓存写入频率 | 每 3 字段写一次替代每字段 |
| 文件树缓存 | 数据库缓存避免重复目录遍历 |
| PDF 提取优化 | 优先读 full_text 避免重复解析 |

### 代码质量
| 优化项 | 影响 |
|--------|------|
| 静默异常修复 | 全部添加 logger.debug() |
| 错误响应格式统一 | 所有 API 返回 {detail, code} |
| 返回类型注解 | 所有路由端点补充类型 |
| 类型化模型返回 | database.py 返回 Paper/AnalysisResult 替代 dict |
| ErrorBoundary | 8 个路由级错误边界 |
| catch 类型安全 | 27+ 处 any → unknown |
| LoadingSpinner 统一 | 6 个页面使用共享组件 |
| ConfirmDialog 替换 | 所有 confirm() → ConfirmDialog |
| TestButton 统一 | 移除重复、CSS 变量适配 |
| SettingsPage 拆分 | 1306 行 → 206 行 + 7 个独立组件 |

### 构建体积
| Chunk | 优化前 | 优化后 | 变化 |
|-------|--------|--------|------|
| CodePage | 243.24 kB | 195.67 kB | -47.57 kB |
| vendor-mermaid | 340.52 kB | 230.13 kB | -110.39 kB |
| vendor-hljs | (内嵌) | 20.78 kB | 独立核心 |

---

## 提交历史

| 版本 | 提交信息 | 日期 |
|------|---------|------|
| v1.13.0 | 性能优化：后端数据库层 + 阻塞IO + 前端懒加载 + 渲染优化 | 2026-06-09 |
| v1.14.0 | 代码质量：后端错误处理 + 前端ErrorBoundary + 一致性修复 | 2026-06-09 |
| v1.15.0 | 深度优化：类型化返回 + SettingsPage拆分 + bundle优化 | 2026-06-09 |
| v1.16.0 | 进一步优化：code.py缓存 + React.memo + SSE批量更新 + 搜索防抖 | 2026-06-09 |
| v1.17.0 | 最终清理：smart_search SQL优化 + 前端catch类型安全 + confirm替换 | 2026-06-09 |

---

*最后更新: 2026-06-09 迭代 5 完成 — 5 个迭代、21 个工作单元*
