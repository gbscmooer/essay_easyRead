# 论文精读助手

面向 CV/ML 研究者的论文精读 Web 应用。上传 PDF 论文，自动解析为结构化内容：核心贡献、方法论、实验设置、代码映射、面试问答。支持关联代码仓库进行代码分析。

## 功能

- **论文解析**: 上传 PDF → AI 自动提取标题、摘要、方法论、实验结果等结构化信息
- **流式输出**: 解析过程实时推送，字段完成即渲染
- **大纲导航**: 论文大纲支持点击章节跳转 PDF 对应页面
- **实验可视化**: 实验结果表格通过 Recharts 图表展示
- **代码分析**: 关联 GitHub 仓库，AI 分析数据流、模块依赖、论文-代码映射
- **相关论文**: 通过 Semantic Scholar 自动搜索相关论文
- **文本操作**: PDF 选中文本即时翻译或 AI 总结
- **面试问答**: 自动生成面试 Q&A 卡片
- **Markdown 导出**: 解析结果导出为 Markdown 文件
- **内置终端**: 底部终端面板支持后端日志、前端日志、PTY 控制台

## 技术栈

- **前端**: React 18 + TypeScript + Tailwind CSS + Vite + Recharts + Mermaid + xterm.js
- **后端**: Python + FastAPI + SQLModel + PyMuPDF + Anthropic SDK + SQLite
- **外部服务**: Semantic Scholar API（相关论文搜索）

## 快速启动

### 环境要求

- Python 3.10+
- Node.js 18+

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 MIMO_API_KEY（必需）
```

也可以启动后在 `/settings` 页面配置 AI 模型。

### 2. 一键启动

```bash
./start.sh
```

### 或分别启动

**后端:**
```bash
cd backend
pip install -r requirements.txt
python -m backend
# 或
uvicorn main:app --reload --port 8000
```

**前端:**
```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 项目结构

详见 `docs/SPECIFICATIONS.md`
