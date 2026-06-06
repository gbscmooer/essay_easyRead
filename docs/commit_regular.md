## 提交流程规范

每次提交代码前，必须严格遵循以下流程。

---

### 一、确定版本号

先查看最近一次 `git log`，获取当前版本号，然后根据变更内容判定递进位：

```
X.Y.Z
│ │ └── Z: Bug 修复、UI 调整、小交互优化
│ └──── Y: 新功能添加、功能级优化（Y 进位时 Z 清零）
└────── X: 重构 / 架构级递进（X 进位时 Y、Z 全部清零）
```

**示例**：
- 1.2.3 → 修复 bug → **1.2.4**
- 1.2.3 → 新增功能 → **1.3.0**
- 1.2.3 → 大规模重构 → **2.0.0**

---

### 二、同步更新版本信息（必须）

以下文件中的版本号与更新日志 **必须** 在提交前同步修改：

| # | 文件 | 需更新内容 |
|---|------|-----------|
| 1 | `frontend/src/components/VersionPopup.tsx` | `APP_VERSION` 常量 → 新版本号 |
|   |  | `CURRENT_RELEASE.date` → 提交日期 |
|   |  | `CURRENT_RELEASE.changes` → 本次更新的功能摘要数组 |
| 2 | `frontend/package.json` | `"version"` 字段 → 新版本号 |
| 3 | `frontend/src/components/TerminalPanel.tsx` | 已引用 `APP_VERSION` 常量，无需单独修改 |

> **注意**：VersionPopup 中的 `CURRENT_RELEASE.changes` 只保留当前版本的更新内容，不展示历史版本。每条用简洁的中文描述，面向用户可读。

---

### 三、编写 Commit Message

格式：`<type>: v<版本号> <简要描述>`

type 可选值：
- `feat` — 新功能
- `fix` — Bug 修复
- `refactor` — 重构
- `ui` — 纯 UI/样式调整
- `docs` — 文档
- `chore` — 杂项

正文用中文列出主要变更点。

---

### 四、检查清单

提交前确认以下全部通过：

- [ ] 前端 `npx tsc --noEmit` 编译 0 错误
- [ ] 后端所有模块可正常 import
- [ ] VersionPopup 版本号和更新日志已同步
- [ ] package.json 版本号已同步
- [ ] commit message 符合格式规范
