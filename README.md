# 论文精读助手

## 安装

```bash
cp .env.example .env
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
cd backend
pip install -r requirements.txt
cd ../frontend
npm install
```

## 启动

### 一键启动

```bash
./start.sh
```

### 分别启动

```bash
source .venv/bin/activate
python -m backend
```

```bash
cd frontend
npm run dev
```

### MCP 服务

```bash
python -m backend.mcp_server
```
