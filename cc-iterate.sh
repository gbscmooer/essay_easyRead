#!/usr/bin/env bash
#
# cc-iterate.sh — Claude Code 自动迭代脚本
#
# 用法:
#   ./cc-iterate.sh "你的初始提示词" -epoch 10
#   ./cc-iterate.sh "仅关注前端的人机交互与 UI 设计，优化体验" -epoch 10
#   ./cc-iterate.sh "优化后端 API 性能" -epoch 5 -test "pytest"
#
# 行为:
#   - 每轮注入默认的 plan -> work -> review -> commit 流程提示
#   - 思考力度走 max（提示词内 ultrathink 关键词触发）
#   - 全程自动批准（--dangerously-skip-permissions）
#   - 每轮跑类型检查，失败则让 Claude 自行修复
#   - 每轮自动 git commit（遵循 docs/commit_regular.md 规范）
#
set -uo pipefail

# ---------- 参数解析 ----------
USER_PROMPT=""
EPOCH=5
TEST_CMD="npx tsc --noEmit"
LOG="overnight-$(date +%Y%m%d-%H%M%S).log"
MODEL="sonnet"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -epoch)      EPOCH="$2"; shift 2 ;;
    -test)       TEST_CMD="$2"; shift 2 ;;
    -model)      MODEL="$2"; shift 2 ;;
    -h|--help)
      echo "用法: ./cc-iterate.sh \"提示词\" -epoch 10"
      echo ""
      echo "选项:"
      echo "  -epoch N     迭代轮次（默认 5）"
      echo "  -test CMD    验证命令（默认 npx tsc --noEmit）"
      echo "  -model NAME  模型名称（默认 sonnet）"
      echo "  -h, --help   显示帮助"
      exit 0
      ;;
    *)           USER_PROMPT="$1"; shift ;;
  esac
done

if [[ -z "$USER_PROMPT" ]]; then
  echo "错误: 缺少初始提示词。用法: ./cc-iterate.sh \"提示词\" -epoch 10"
  exit 1
fi

# ---------- 默认流程提示词 ----------
DEFAULT_FLOW=$(cat <<'EOF'
请严格遵循以下 CI/CD 友好的工作流，并在思考时使用 ultrathink 级别的深度推理:

1. PLAN  — 先阅读相关代码，制定本轮要做的具体、可验证的改动清单。改动要小而聚焦，单轮只推进一个明确目标。
2. WORK  — 按计划实施改动。保持代码风格一致，不破坏现有功能。
3. REVIEW— 自我审查本轮 diff: 是否引入回归? 是否符合项目约定? 边界情况是否处理? 发现问题立即修正。
4. COMMIT— 确保改动完整且可提交。不要自行执行 git commit，由外层脚本统一处理。

提交规范（每轮必须遵守）:
- 提交前运行 npx tsc --noEmit 确保类型检查通过
- 若涉及前端改动，需同步更新 frontend/src/components/VersionPopup.tsx 中的 APP_VERSION 和 CURRENT_RELEASE
- 同步更新 frontend/package.json 中的 version 字段
- 版本号规则: X.Y.Z — bug/UI调整递进Z位，新功能递进Y位(Z清零)，重构递进X位(Y/Z清零)
- commit message 格式: <type>: v<版本号> <简要描述>，type 可选 feat/fix/refactor/ui/docs/chore

约束:
- 不要询问用户，自主完成本轮全部决策。
- 若本轮目标已在之前轮次完成，则寻找下一个最有价值的小改进，不要空转。
- 不要执行危险的、不可逆的系统操作（删除非项目文件、修改 git 历史等）。
- 不要自行执行 git add/commit，由外层脚本处理。
EOF
)

# ---------- 颜色 ----------
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

# ---------- 统计 ----------
COMMITS=0
FAILURES=0
START_TIME=$(date +%s)

# ---------- 主循环 ----------
echo -e "${GREEN}========================================${NC}" | tee -a "$LOG"
echo -e "${GREEN}  Claude Code 自动迭代${NC}" | tee -a "$LOG"
echo -e "${GREEN}========================================${NC}" | tee -a "$LOG"
echo -e "  epoch : ${BLUE}$EPOCH${NC}" | tee -a "$LOG"
echo -e "  model : ${BLUE}$MODEL${NC}" | tee -a "$LOG"
echo -e "  test  : ${BLUE}$TEST_CMD${NC}" | tee -a "$LOG"
echo -e "  log   : ${DIM}$LOG${NC}" | tee -a "$LOG"
echo -e "  prompt: ${USER_PROMPT}" | tee -a "$LOG"
echo -e "${GREEN}========================================${NC}" | tee -a "$LOG"
echo "" | tee -a "$LOG"

for ((i=1; i<=EPOCH; i++)); do
  EPOCH_START=$(date +%s)
  echo "" | tee -a "$LOG"
  echo -e "${BLUE}########## EPOCH $i / $EPOCH | $(date '+%H:%M:%S') ##########${NC}" | tee -a "$LOG"

  FULL_PROMPT="本轮迭代目标（第 $i / $EPOCH 轮）:
$USER_PROMPT

$DEFAULT_FLOW"

  # --- 主迭代 ---
  claude -p "$FULL_PROMPT" \
    --model "$MODEL" \
    --dangerously-skip-permissions \
    --output-format stream-json --verbose \
    2>&1 | tee -a "$LOG"

  CLAUDE_EXIT=${PIPESTATUS[0]}
  if [[ $CLAUDE_EXIT -ne 0 ]]; then
    echo -e "${RED}>>> EPOCH $i Claude 执行异常 (exit=$CLAUDE_EXIT)，继续下一轮${NC}" | tee -a "$LOG"
    FAILURES=$((FAILURES + 1))
    continue
  fi

  # --- 类型检查/测试 + 失败自修（最多 2 次）---
  TEST_PASSED=false
  for attempt in 1 2; do
    echo -e "${DIM}>>> 运行验证: $TEST_CMD (第 $attempt 次)${NC}" | tee -a "$LOG"
    if eval "$TEST_CMD" >>"$LOG" 2>&1; then
      echo -e "${GREEN}>>> EPOCH $i 验证通过${NC}" | tee -a "$LOG"
      TEST_PASSED=true
      break
    else
      echo -e "${RED}>>> EPOCH $i 验证失败（第 $attempt 次），让 Claude 修复${NC}" | tee -a "$LOG"
      claude -p "运行 '$TEST_CMD' 后验证失败。请查看 $LOG 末尾的错误输出，定位并修复问题。不要绕过或删除检查。使用 ultrathink 深度推理。" \
        --model "$MODEL" \
        --dangerously-skip-permissions \
        --output-format stream-json --verbose \
        2>&1 | tee -a "$LOG"
    fi
  done

  if ! $TEST_PASSED; then
    echo -e "${RED}>>> EPOCH $i 验证始终失败，跳过提交，继续下一轮${NC}" | tee -a "$LOG"
    FAILURES=$((FAILURES + 1))
    continue
  fi

  # --- 每轮自动 commit ---
  if [[ -n "$(git status --porcelain)" ]]; then
    git add -A
    git commit -m "auto-iter epoch $i/$EPOCH: ${USER_PROMPT:0:60}" >>"$LOG" 2>&1
    COMMITS=$((COMMITS + 1))
    echo -e "${GREEN}>>> EPOCH $i 已提交${NC}" | tee -a "$LOG"
  else
    echo -e "${YELLOW}>>> EPOCH $i 无改动，跳过提交${NC}" | tee -a "$LOG"
  fi

  EPOCH_END=$(date +%s)
  EPOCH_SECS=$((EPOCH_END - EPOCH_START))
  echo -e "${DIM}>>> EPOCH $i 耗时: ${EPOCH_SECS}s${NC}" | tee -a "$LOG"

  sleep 2
done

# ---------- 摘要 ----------
END_TIME=$(date +%s)
TOTAL_SECS=$((END_TIME - START_TIME))
TOTAL_MINS=$((TOTAL_SECS / 60))

echo "" | tee -a "$LOG"
echo -e "${GREEN}========================================${NC}" | tee -a "$LOG"
echo -e "${GREEN}  迭代完成 — 摘要${NC}" | tee -a "$LOG"
echo -e "${GREEN}----------------------------------------${NC}" | tee -a "$LOG"
echo -e "  总轮次 : $EPOCH" | tee -a "$LOG"
echo -e "  提交数 : ${GREEN}$COMMITS${NC}" | tee -a "$LOG"
echo -e "  失败数 : ${RED}$FAILURES${NC}" | tee -a "$LOG"
echo -e "  总耗时 : ${TOTAL_MINS}m $((TOTAL_SECS % 60))s" | tee -a "$LOG"
echo -e "${GREEN}----------------------------------------${NC}" | tee -a "$LOG"
echo -e "  日志   : $LOG" | tee -a "$LOG"
echo -e "  历史   : git log --oneline -$EPOCH" | tee -a "$LOG"
echo -e "${GREEN}========================================${NC}" | tee -a "$LOG"
