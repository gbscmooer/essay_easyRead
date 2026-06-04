# 论文精读助手

面向 CV/ML 研究者的论文精读 Web 应用。上传 PDF 论文，自动解析为结构化内容：核心贡献、方法论、实验设置、代码映射、面试问答。

## 技术栈

- **前端**: React + TypeScript + Tailwind CSS + PDF.js + Recharts
- **后端**: Python + FastAPI + PyMuPDF + MiMo API + SQLite

## 快速启动

### 后端

```bash
cd backend
cp ../.env.example .env
# 编辑 .env 填入 MIMO_API_KEY
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 项目结构

详见 `docs/SPECIFICATIONS.md`
