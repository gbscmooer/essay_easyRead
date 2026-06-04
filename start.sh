#!/bin/bash

# 论文精读助手 - 一键启动脚本
# 使用方法: ./start.sh
# 关闭: Ctrl+C 会自动关闭前后端

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 子进程 PID
BACKEND_PID=""
FRONTEND_PID=""

# 清理函数
cleanup() {
    echo ""
    echo -e "${RED}正在关闭服务...${NC}"

    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
        echo -e "${BLUE}后端已关闭${NC}"
    fi

    if [ -n "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
        echo -e "${BLUE}前端已关闭${NC}"
    fi

    # 等待子进程退出
    wait 2>/dev/null

    echo -e "${GREEN}已全部关闭${NC}"
    exit 0
}

# 注册清理函数
trap cleanup SIGINT SIGTERM EXIT

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}      论文精读助手 - 启动中...${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 启动后端
echo -e "${BLUE}[1/2] 启动后端...${NC}"
cd "$SCRIPT_DIR"
python -m backend &
BACKEND_PID=$!
echo -e "${GREEN}      后端 PID: $BACKEND_PID${NC}"

# 等待后端启动
sleep 2

# 启动前端
echo -e "${BLUE}[2/2] 启动前端...${NC}"
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}      前端 PID: $FRONTEND_PID${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}      启动完成!${NC}"
echo -e "${GREEN}      前端: http://localhost:5173${NC}"
echo -e "${GREEN}      后端: http://localhost:8000${NC}"
echo -e "${GREEN}      按 Ctrl+C 关闭所有服务${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 等待子进程（这样 Ctrl+C 可以被捕获）
wait
