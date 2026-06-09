#!/bin/bash

# 论文精读助手 - 一键启动脚本
# 使用方法:
#   ./start.sh              启动全部服务（后端 + 前端 + GROBID）
#   ./start.sh --no-grobid  跳过 GROBID（不需要深度研究引用解析时更快启动）
#   ./start.sh --backend-only  仅启动后端
#   ./start.sh --frontend-only 仅启动前端
#
# 关闭: Ctrl+C 自动关闭所有服务

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# ============================================================
# 参数解析
# ============================================================
START_BACKEND=true
START_FRONTEND=true
START_GROBID=true

for arg in "$@"; do
  case "$arg" in
    --no-grobid)      START_GROBID=false ;;
    --backend-only)   START_FRONTEND=false; START_GROBID=false ;;
    --frontend-only)  START_BACKEND=false; START_GROBID=false ;;
    -h|--help)
      echo "用法: ./start.sh [选项]"
      echo ""
      echo "选项:"
      echo "  --no-grobid       跳过 GROBID 容器（深度研究引用解析）"
      echo "  --backend-only    仅启动后端"
      echo "  --frontend-only   仅启动前端"
      echo "  -h, --help        显示帮助"
      exit 0
      ;;
    *)
      echo "未知参数: $arg (使用 -h 查看帮助)"
      exit 1
      ;;
  esac
done

# ============================================================
# 颜色
# ============================================================
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

# ============================================================
# 端口配置
# ============================================================
BACKEND_PORT=8000
FRONTEND_PORT=5173

# ============================================================
# 子进程 PID 跟踪
# ============================================================
BACKEND_PID=""
FRONTEND_PID=""

# GROBID 配置
GROBID_URL="http://localhost:8070"
GROBID_IMAGE="grobid/grobid:0.9.0-crf"
GROBID_CONTAINER="grobid"
GROBID_ACTION=""

grobid_alive() { curl -sf "${GROBID_URL}/api/isalive" 2>/dev/null | grep -qi true; }
docker_running() { docker info >/dev/null 2>&1; }

# ============================================================
# 端口占用检查
# ============================================================
check_port() {
  local port=$1
  local name=$2
  if lsof -i :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo -e "${RED}  端口 $port 已被占用（$name 需要此端口）${NC}"
    echo -e "${DIM}  运行 lsof -i :$port 查看占用进程${NC}"
    return 1
  fi
  return 0
}

# ============================================================
# 健康检查：等待 HTTP 端点可达
# ============================================================
wait_for_service() {
  local url=$1
  local name=$2
  local max_wait=${3:-30}
  local elapsed=0

  while [ $elapsed -lt $max_wait ]; do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

# ============================================================
# 终止进程及其子进程（跨平台）
# ============================================================
kill_process_tree() {
  local pid=$1
  local name=$2

  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  # 先杀子进程（npm 等会 fork 子进程）
  pkill -P "$pid" 2>/dev/null || true

  # 再杀主进程
  kill "$pid" 2>/dev/null || true

  # 等待退出（最多 3 秒）
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ $waited -lt 3 ]; do
    sleep 0.5
    waited=$((waited + 1))
  done

  # 仍未退出则强制杀
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi

  echo -e "${BLUE}  $name 已关闭${NC}"
}

# ============================================================
# 清理函数 — Ctrl+C 或脚本退出时调用
# ============================================================
cleanup() {
  echo ""
  echo -e "${RED}正在关闭所有服务...${NC}"

  kill_process_tree "$BACKEND_PID" "后端"
  kill_process_tree "$FRONTEND_PID" "前端"

  # 关闭 GROBID 容器（仅关本脚本启动的）
  if [ "$GROBID_ACTION" = "created" ]; then
    docker stop "$GROBID_CONTAINER" >/dev/null 2>&1 || true
    docker rm "$GROBID_CONTAINER" >/dev/null 2>&1 || true
    echo -e "${BLUE}  GROBID 容器已关闭并移除${NC}"
  elif [ "$GROBID_ACTION" = "started" ]; then
    docker stop "$GROBID_CONTAINER" >/dev/null 2>&1 || true
    echo -e "${BLUE}  GROBID 容器已停止${NC}"
  fi

  wait 2>/dev/null
  echo -e "${GREEN}已全部关闭${NC}"
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# ============================================================
# 自动激活 Python 虚拟环境
# ============================================================
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

# ============================================================
# 启动
# ============================================================
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}      论文精读助手 - 启动中...${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

STEP=0
TOTAL=0
$START_BACKEND  && TOTAL=$((TOTAL + 1))
$START_FRONTEND && TOTAL=$((TOTAL + 1))
$START_GROBID   && TOTAL=$((TOTAL + 1))

# ---- 后端 ----
if $START_BACKEND; then
  STEP=$((STEP + 1))
  echo -e "${BLUE}[$STEP/$TOTAL] 启动后端 (port $BACKEND_PORT)...${NC}"

  if ! check_port $BACKEND_PORT "后端"; then
    echo -e "${RED}  后端启动跳过${NC}"
  else
    cd "$SCRIPT_DIR"
    python -m backend &
    BACKEND_PID=$!

    if wait_for_service "http://localhost:$BACKEND_PORT/" "后端" 15; then
      echo -e "${GREEN}      后端就绪 (PID: $BACKEND_PID)${NC}"
    else
      echo -e "${YELLOW}      后端启动较慢，正在后台继续初始化...${NC}"
    fi
  fi
fi

# ---- 前端 ----
if $START_FRONTEND; then
  STEP=$((STEP + 1))
  echo -e "${BLUE}[$STEP/$TOTAL] 启动前端 (port $FRONTEND_PORT)...${NC}"

  if ! check_port $FRONTEND_PORT "前端"; then
    echo -e "${RED}  前端启动跳过${NC}"
  else
    cd "$SCRIPT_DIR/frontend"
    npm run dev &
    FRONTEND_PID=$!
    cd "$SCRIPT_DIR"

    if wait_for_service "http://localhost:$FRONTEND_PORT/" "前端" 20; then
      echo -e "${GREEN}      前端就绪 (PID: $FRONTEND_PID)${NC}"
    else
      echo -e "${YELLOW}      前端启动较慢，正在后台继续初始化...${NC}"
    fi
  fi
fi

# ---- GROBID ----
if $START_GROBID; then
  STEP=$((STEP + 1))
  echo -e "${BLUE}[$STEP/$TOTAL] 启动 GROBID (深度研究引用解析)...${NC}"

  if grobid_alive; then
    echo -e "${GREEN}      GROBID 已在运行，复用现有服务${NC}"
  elif command -v docker >/dev/null 2>&1; then
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
        echo -e "${GREEN}      GROBID 容器已启动（Java 服务约 30-60s ready）${NC}"
      else
        echo -e "${RED}      GROBID 启动失败，深度研究将回退正则解析${NC}"
      fi
    else
      echo -e "${RED}      Docker 守护进程未就绪，深度研究将回退正则解析${NC}"
    fi
  else
    echo -e "${RED}      未安装 Docker，深度研究将回退正则解析${NC}"
  fi
fi

# ============================================================
# 启动完成
# ============================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}      启动完成!${NC}"
echo -e "${GREEN}----------------------------------------${NC}"
$START_BACKEND  && echo -e "${GREEN}      后端: http://localhost:$BACKEND_PORT${NC}"
$START_FRONTEND && echo -e "${GREEN}      前端: http://localhost:$FRONTEND_PORT${NC}"
$START_GROBID   && echo -e "${GREEN}      GROBID: http://localhost:8070${NC}"
echo -e "${GREEN}----------------------------------------${NC}"
echo -e "${DIM}      按 Ctrl+C 关闭所有服务${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

wait
