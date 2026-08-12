#!/usr/bin/env bash
#
# 새 커밋이 있을 때만 배포하고 봇을 재시작합니다.
# systemd 타이머가 주기적으로 실행하고, 언제든 직접 돌려도 됩니다:
#
#   bash /opt/mahjong-bot/deploy/auto-update.sh
#   sudo systemctl start mahjong-update      # 타이머를 즉시 한 번 실행
#
# 변경이 없으면 아무것도 하지 않아요 (봇도 그대로 유지).
#
set -uo pipefail

REPO_SLUG="${REPO_SLUG:-s2selyn/I_WANNA_PLAY_MAHJONG}"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/opt/mahjong-bot}"
BOT_UNIT=mahjong-bot
STATE_DIR="$DEST/data"          # gitignore 대상이라 배포해도 안 지워져요
STATE="$STATE_DIR/deployed_sha"

log() { echo "[$(date '+%F %T')] $*"; }

# 타이머는 root 로 돌고, 사람이 직접 실행할 땐 일반 계정이에요. 둘 다 되게.
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
export BOT_USER="${BOT_USER:-$(stat -c '%U' "$DEST" 2>/dev/null || echo "$USER")}"

# 응답의 첫 "sha" 가 커밋 해시예요. sed 로 잡으면 탐욕적 매칭 탓에 뒤쪽
# tree sha 를 집어오므로, grep -o 로 앞에서부터 첫 매치만 씁니다.
remote_sha="$(curl -fsSL -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${REPO_SLUG}/commits/${BRANCH}" \
  | grep -o '"sha" *: *"[0-9a-f]\{40\}"' | head -1 \
  | grep -o '[0-9a-f]\{40\}')"

if [ -z "$remote_sha" ]; then
  log "원격 커밋을 못 읽었어요 (네트워크/레이트리밋?). 이번 회차는 건너뜁니다."
  exit 0
fi

local_sha="$(cat "$STATE" 2>/dev/null || true)"
if [ "$remote_sha" = "$local_sha" ]; then
  log "변경 없음 (${remote_sha:0:7}) — 그대로 둡니다."
  exit 0
fi

log "새 버전 발견: ${local_sha:0:7}${local_sha:+ }→ ${remote_sha:0:7}"
if ! bash "$DEST/deploy/setup.sh"; then
  log "배포 실패 — 봇은 건드리지 않고 종료합니다."
  exit 1
fi

if ! $SUDO systemctl restart "$BOT_UNIT"; then
  # 기록을 남기지 않아야 다음 회차에 다시 시도해요
  log "재시작 실패 — 다음 주기에 다시 시도합니다."
  exit 1
fi
mkdir -p "$STATE_DIR"
echo "$remote_sha" > "$STATE"
log "배포 완료 및 재시작 (${remote_sha:0:7})"
