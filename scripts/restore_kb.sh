#!/usr/bin/env bash
#
# 数据卷恢复脚本（配合 backup_kb.sh）。
#
# 覆盖**两个**卷（与备份脚本对称）：
#   - sekb_data        ：知识库资产（chroma_db / uploads / conversations / news ...）
#   - sekb_gitea_data  ：版本管理权威源（仓库 / SQLite / 配置）——丢了就丢源码
#
# 用法：
#   restore_kb.sh <backup.tar.gz>            # 按文件名自动识别恢复哪个卷
#   restore_kb.sh --sekb  <backup.tar.gz>    # 只恢复 sekb_data
#   restore_kb.sh --gitea <backup.tar.gz>    # 只恢复 sekb_gitea_data
#   restore_kb.sh --both  <backup.tar.gz>    # 恢复两个卷（自动配对同时间戳的另一半）
#   restore_kb.sh --dry-run <backup.tar.gz>  # 只做预检/完整性校验，不动任何数据
#   restore_kb.sh --drill --gitea <backup>   # 演练：恢复到**旁路卷**，不碰线上数据/服务
#
# 演练模式（--drill，配合环境变量指定旁路卷）：
#   RESTORE_SEKB_VOLUME=sekb_drill_data RESTORE_GITEA_VOLUME=sekb_drill_gitea \
#     restore_kb.sh --drill --both <backup.tar.gz>
#   → 不停止/重启任何线上服务，只把备份回灌进旁路卷，供校验后删除。
#   ⚠️ 安全联锁：--drill **拒绝**写入生产卷 sekb_data / sekb_gitea_data 而退出。
#
# 示例：
#   restore_kb.sh /opt/self-evolving-kb/backups/sekb_data_20260914_182934.tar.gz
#   restore_kb.sh --both /opt/self-evolving-kb/backups/sekb_data_20260914_182934.tar.gz
#
# 关键保障：
#   1. `trap ... EXIT` 兜底：无论成功/失败，退出前确保**被停掉的服务**重新启动；
#   2. `--both` 会**先校验两个备份都存在且可解包**再动手，杜绝「恢复了一半」的半成品状态；
#   3. 解包前先 `tar tzf` 校验归档可读（对应审查项 OPS-12「备份无完整性校验」）；
#   4. 确认目标卷**已存在**才恢复（否则 `docker run -v` 会静默创建空卷，把故障掩盖成"恢复成功"）。
#
# 注意：恢复会**覆盖**目标卷现有内容，请确认其中没有未备份的新数据。
#
set -euo pipefail

COMPOSE_DIR="/opt/self-evolving-kb/SelfEvolvingKnowledgeBase"
BACKUP_DIR="/opt/self-evolving-kb/backups"
# 卷名可用环境变量覆盖，供「旁路卷演练」使用（见 --drill）
SEKB_VOLUME="${RESTORE_SEKB_VOLUME:-sekb_data}"
GITEA_VOLUME="${RESTORE_GITEA_VOLUME:-sekb_gitea_data}"
PROD_SEKB_VOLUME="sekb_data"
PROD_GITEA_VOLUME="sekb_gitea_data"

MODE=""
DRY_RUN=false
DRILL=false
BACKUP_FILE=""

usage() {
  # 跳过 shebang（第 1 行），只输出注释头部
  sed -n '2,$p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
}

# ------------------------------------------------------------
# 1) 解析参数
# ------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --both)    MODE="both";  shift ;;
    --gitea)   MODE="gitea"; shift ;;
    --sekb)    MODE="sekb";  shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --drill)   DRILL=true;   shift ;;
    -h|--help) usage; exit 0 ;;
    -*)        echo "未知参数: $1" >&2; usage >&2; exit 1 ;;
    *)
      if [ -n "${BACKUP_FILE}" ]; then
        echo "只接受一个备份文件参数（已收到: ${BACKUP_FILE}）" >&2; exit 1
      fi
      BACKUP_FILE="$1"; shift ;;
  esac
done

if [ -z "${BACKUP_FILE}" ]; then
  echo "用法: $0 [--sekb|--gitea|--both] [--dry-run] <backup_file.tar.gz>" >&2
  exit 1
fi
if [ ! -f "${BACKUP_FILE}" ]; then
  echo "备份文件不存在: ${BACKUP_FILE}" >&2
  exit 1
fi

# ------------------------------------------------------------
# 2) 从文件名解析时间戳，定位成对的另一半备份
# ------------------------------------------------------------
BACKUP_FILE_ABS="$(cd "$(dirname "${BACKUP_FILE}")" && pwd)/$(basename "${BACKUP_FILE}")"
BACKUP_DIR_ABS="$(dirname "${BACKUP_FILE_ABS}")"
BASE="$(basename "${BACKUP_FILE_ABS}")"

TS=""
DETECTED=""
if [[ "${BASE}" =~ ^sekb_gitea_data_([0-9]{8}_[0-9]{6})\.tar\.gz$ ]]; then
  TS="${BASH_REMATCH[1]}"; DETECTED="gitea"
elif [[ "${BASE}" =~ ^sekb_data_([0-9]{8}_[0-9]{6})\.tar\.gz$ ]]; then
  TS="${BASH_REMATCH[1]}"; DETECTED="sekb"
else
  echo "无法识别的备份文件名: ${BASE}" >&2
  echo "  期望形如 sekb_data_YYYYMMDD_HHMMSS.tar.gz 或 sekb_gitea_data_YYYYMMDD_HHMMSS.tar.gz" >&2
  exit 1
fi

SEKB_FILE="${BACKUP_DIR_ABS}/sekb_data_${TS}.tar.gz"
GITEA_FILE="${BACKUP_DIR_ABS}/sekb_gitea_data_${TS}.tar.gz"

# 未显式指定模式时，按文件名自动识别
if [ -z "${MODE}" ]; then
  MODE="${DETECTED}"
fi

DO_SEKB=false
DO_GITEA=false
case "${MODE}" in
  sekb)  DO_SEKB=true ;;
  gitea) DO_GITEA=true ;;
  both)  DO_SEKB=true; DO_GITEA=true ;;
  *)     echo "内部错误: 未知模式 ${MODE}" >&2; exit 1 ;;
esac

echo "备份时间戳: ${TS}（文件名识别为 ${DETECTED}，恢复模式: ${MODE}）"

# ------------------------------------------------------------
# 2.5) 演练模式安全联锁：--drill **绝不允许**写入生产卷
#      （演练不停止线上服务，若误写生产卷就会在不一致状态下覆盖真实数据）
# ------------------------------------------------------------
if [ "${DRILL}" = true ]; then
  if [ "${DO_SEKB}" = true ] && [ "${SEKB_VOLUME}" = "${PROD_SEKB_VOLUME}" ]; then
    echo "❌ --drill 拒绝写入生产卷 ${PROD_SEKB_VOLUME}" >&2
    echo "   演练请指定旁路卷，例如: RESTORE_SEKB_VOLUME=sekb_drill_data $0 --drill ..." >&2
    exit 1
  fi
  if [ "${DO_GITEA}" = true ] && [ "${GITEA_VOLUME}" = "${PROD_GITEA_VOLUME}" ]; then
    echo "❌ --drill 拒绝写入生产卷 ${PROD_GITEA_VOLUME}" >&2
    echo "   演练请指定旁路卷，例如: RESTORE_GITEA_VOLUME=sekb_drill_gitea $0 --drill ..." >&2
    exit 1
  fi
  echo "⚠️  演练模式：不停止/重启任何线上服务，仅写入旁路卷"
  if [ "${DO_SEKB}" = true ]; then echo "     - ${SEKB_VOLUME} <- $(basename "${SEKB_FILE}")"; fi
  if [ "${DO_GITEA}" = true ]; then echo "     - ${GITEA_VOLUME} <- $(basename "${GITEA_FILE}")"; fi
fi

# ------------------------------------------------------------
# 3) 预检：文件成对存在 + 目标卷存在（**全部通过才开始动手**）
# ------------------------------------------------------------
if [ "${DO_SEKB}" = true ] && [ ! -f "${SEKB_FILE}" ]; then
  echo "❌ 缺少 sekb_data 备份: ${SEKB_FILE}" >&2
  exit 1
fi
if [ "${DO_GITEA}" = true ] && [ ! -f "${GITEA_FILE}" ]; then
  echo "❌ 缺少 sekb_gitea_data 备份: ${GITEA_FILE}" >&2
  echo "   提示: --both/--gitea 需要同时间戳(TS=${TS})的成对文件。" >&2
  exit 1
fi

if [ "${DO_SEKB}" = true ] && ! docker volume inspect "${SEKB_VOLUME}" >/dev/null 2>&1; then
  echo "❌ 数据卷不存在: ${SEKB_VOLUME}（不会自动创建，以免把失败误判为成功）" >&2
  exit 1
fi
if [ "${DO_GITEA}" = true ] && ! docker volume inspect "${GITEA_VOLUME}" >/dev/null 2>&1; then
  echo "❌ 数据卷不存在: ${GITEA_VOLUME}（不会自动创建，以免把失败误判为成功）" >&2
  exit 1
fi

# 完整性校验：归档可被 tar 读取（早失败，避免清空卷后才发现备份损坏）
check_archive() {
  local f="$1"
  echo "校验归档可读: $(basename "${f}") ..."
  if ! docker run --rm -v "${BACKUP_DIR_ABS}:/backup:ro" alpine \
       tar tzf "/backup/$(basename "${f}")" >/dev/null 2>&1; then
    echo "❌ 归档无法解包（可能已损坏）: ${f}" >&2
    return 1
  fi
  return 0
}

if [ "${DO_SEKB}" = true ]; then check_archive "${SEKB_FILE}"; fi
if [ "${DO_GITEA}" = true ]; then check_archive "${GITEA_FILE}"; fi

if [ "${DRY_RUN}" = true ]; then
  echo "✅ [dry-run] 预检通过，未改动任何数据。将恢复："
  if [ "${DO_SEKB}" = true ]; then
    echo "   - ${SEKB_VOLUME} <- $(basename "${SEKB_FILE}")"
  fi
  if [ "${DO_GITEA}" = true ]; then
    echo "   - ${GITEA_VOLUME} <- $(basename "${GITEA_FILE}")"
  fi
  exit 0
fi

# 演练模式不调用 compose，因此不依赖仓库目录存在
if [ "${DRILL}" = false ]; then
  cd "${COMPOSE_DIR}"
fi

# ------------------------------------------------------------
# 4) 兜底：只重启**本次真正停掉**的服务
# ------------------------------------------------------------
STOPPED_SEKB=false
STOPPED_GITEA=false

restart_services() {
  # 演练模式 / 未停服时静默（不打印误导性的「已停服务」提示）
  if [ "${STOPPED_SEKB}" = true ]; then
    echo "[$(date +%Y%m%d_%H%M%S)] 兜底：重启 backend"
    docker compose -f docker-compose.prod.yml --env-file .env.prod start backend 2>/dev/null || true
  fi
  if [ "${STOPPED_GITEA}" = true ]; then
    echo "[$(date +%Y%m%d_%H%M%S)] 兜底：重启 gitea"
    docker compose -f docker-compose.monitoring.yml start gitea 2>/dev/null || true
  fi
  return 0
}
trap restart_services EXIT

restore_volume() {
  local vol="$1" file="$2"
  echo "清空数据卷 ${vol} ..."
  docker run --rm -v "${vol}:/data" alpine \
    sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true'
  echo "解包 $(basename "${file}") -> ${vol} ..."
  docker run --rm \
    -v "${vol}:/data" \
    -v "${BACKUP_DIR_ABS}:/backup:ro" \
    alpine sh -c "tar xzf \"/backup/$(basename "${file}")\" -C /data"
}

# ------------------------------------------------------------
# 5) 按序恢复
# ------------------------------------------------------------
if [ "${DO_SEKB}" = true ]; then
  if [ "${DRILL}" = false ]; then
    echo "停止 backend ..."
    docker compose -f docker-compose.prod.yml --env-file .env.prod stop backend
    STOPPED_SEKB=true
  fi
  restore_volume "${SEKB_VOLUME}" "${SEKB_FILE}"
  if [ "${DRILL}" = false ]; then
    echo "重启 backend ..."
    docker compose -f docker-compose.prod.yml --env-file .env.prod start backend
  fi
fi

if [ "${DO_GITEA}" = true ]; then
  if [ "${DRILL}" = false ]; then
    echo "停止 gitea ..."
    docker compose -f docker-compose.monitoring.yml stop gitea
    STOPPED_GITEA=true
  fi
  restore_volume "${GITEA_VOLUME}" "${GITEA_FILE}"
  if [ "${DRILL}" = false ]; then
    echo "重启 gitea ..."
    docker compose -f docker-compose.monitoring.yml start gitea
    echo "等待 gitea 就绪并校验 ..."
    for _ in $(seq 1 30); do
      if curl -fsS http://localhost:3000/api/healthz >/dev/null 2>&1; then
        echo "✅ gitea healthz OK"
        break
      fi
      sleep 2
    done
  fi
fi

echo "恢复完成（时间戳 ${TS}，模式 ${MODE}）"
