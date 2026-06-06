#!/bin/bash

# 论文精读助手 - 一键启动脚本
# 使用方法: ./start.sh
# 启动: 后端 + 前端 + GROBID(Docker，深度研究引用解析后端)
# 关闭: Ctrl+C 自动关闭前后端，并停止本脚本启动的 GROBID 容器（不会关闭 Docker 本身）

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 自动激活 Python 虚拟环境
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# 子进程 PID
BACKEND_PID=""
FRONTEND_PID=""

# GROBID（深度研究引用解析后端）配置与关闭状态
GROBID_URL="http://localhost:8070"
# CRF 轻量镜像，0.9.0 提供 arm64 原生（Apple Silicon 不走 Rosetta 模拟，稳定）
# 新版 JDK 自带 cgroup v2 + 模块兼容，无需任何 JVM 补丁
GROBID_IMAGE="grobid/grobid:0.9.0-crf"
GROBID_CONTAINER="grobid"
GROBID_ACTION=""        # ""|created|started —— 本脚本对容器做了什么，决定如何关闭

grobid_alive() { curl -sf "${GROBID_URL}/api/isalive" 2>/dev/null | grep -qi true; }
docker_running() { docker info >/dev/null 2>&1; }

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

    # 关闭 GROBID 容器（仅关本脚本启动的，不动你原有的容器）
    if [ "$GROBID_ACTION" = "created" ]; then
        docker stop "$GROBID_CONTAINER" >/dev/null 2>&1 || true
        docker rm "$GROBID_CONTAINER" >/dev/null 2>&1 || true
        echo -e "${BLUE}GROBID 容器已关闭并移除${NC}"
    elif [ "$GROBID_ACTION" = "started" ]; then
        docker stop "$GROBID_CONTAINER" >/dev/null 2>&1 || true
        echo -e "${BLUE}GROBID 容器已停止${NC}"
    fi
    # 注意：不退出 Docker Desktop 本身——只管本脚本起的 GROBID 容器

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
echo -e "${BLUE}[1/3] 启动后端...${NC}"
cd "$SCRIPT_DIR"
python -m backend &
BACKEND_PID=$!
echo -e "${GREEN}      后端 PID: $BACKEND_PID${NC}"

# 等待后端启动
sleep 2

# 启动前端
echo -e "${BLUE}[2/3] 启动前端...${NC}"
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}      前端 PID: $FRONTEND_PID${NC}"
cd "$SCRIPT_DIR"

# 启动 GROBID（深度研究引用解析后端，端口 8070）
echo -e "${BLUE}[3/3] 启动 GROBID...${NC}"
if grobid_alive; then
    echo -e "${GREEN}      GROBID 已在运行，复用现有服务${NC}"
elif command -v docker >/dev/null 2>&1; then
    # 确保 Docker 守护进程在运行
    if ! docker_running; then
        echo -e "${BLUE}      启动 Docker Desktop（最长等待 60s）...${NC}"
        open -a Docker >/dev/null 2>&1 || true
        for _ in $(seq 1 30); do
            docker_running && break || true
            sleep 2
        done
    fi
    if docker_running; then
        if docker ps -a --format '{{.Names}}' | grep -qx "$GROBID_CONTAINER"; then
            docker start "$GROBID_CONTAINER" >/dev/null 2>&1 && GROBID_ACTION="started" || true
        else
            echo -e "${BLUE}      首次拉取镜像可能数分钟...${NC}"
            docker run -d --name "$GROBID_CONTAINER" -p 8070:8070 \
                "$GROBID_IMAGE" >/dev/null 2>&1 \
                && GROBID_ACTION="created" || true
        fi
        if [ -n "$GROBID_ACTION" ]; then
            echo -e "${GREEN}      GROBID 容器已启动（Java 服务约 30-60s ready，深度研究前就绪即可）${NC}"
        else
            echo -e "${RED}      GROBID 启动失败，深度研究将回退正则解析${NC}"
        fi
    else
        echo -e "${RED}      Docker 守护进程未就绪，深度研究将回退正则解析${NC}"
    fi
else
    echo -e "${RED}      未安装 Docker，深度研究将回退正则解析${NC}"
fi

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
