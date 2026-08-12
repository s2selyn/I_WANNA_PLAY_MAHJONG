#!/usr/bin/env bash
#
# Oracle Cloud 한 방 설치 스크립트 (Ubuntu / Oracle Linux 공용).
# VM 에 SSH 로 접속한 뒤 실행하세요:
#
#   curl -fsSL https://raw.githubusercontent.com/s2selyn/I_WANNA_PLAY_MAHJONG/main/deploy/setup.sh | bash
#
# 또는 레포를 이미 받았다면:  bash deploy/setup.sh
#
# 환경변수:
#   BRANCH=main       배포할 브랜치
#   WITH_FFMPEG=1     음성 효과음용 ffmpeg 도 설치합니다.
#                     기본은 설치하지 않아요 — 램/CPU 가 작은 무료 VM 에서는
#                     EPEL 메타데이터 때문에 아주 오래 걸리거든요.
#
# 설계 원칙: **봇이 먼저 돌아가게** 하고, 무거운 선택 기능(ffmpeg)은 맨 마지막에.
# 이미 있는 건 절대 다시 설치하지 않습니다 (dnf/apt 호출 최소화).
#
set -euo pipefail

REPO="https://github.com/s2selyn/I_WANNA_PLAY_MAHJONG.git"
BRANCH="${BRANCH:-main}"
DEST="/opt/mahjong-bot"   # 레포를 여기에 clone (레포 루트 = 봇 파일)
TARBALL="https://codeload.github.com/s2selyn/I_WANNA_PLAY_MAHJONG/tar.gz/refs/heads/${BRANCH}"

have() { command -v "$1" >/dev/null 2>&1; }

# 봇을 실행할 계정. 보통은 지금 이 스크립트를 돌리는 사람이지만, 자동 배포처럼
# root 로 실행될 때는 BOT_USER 로 알려줍니다 (root 로 봇이 돌아가지 않도록).
BOT_USER="${BOT_USER:-$USER}"

# 이 스크립트는 실행 도중 $DEST 를 통째로 갱신하는데, 그 안에는 자기 자신도 들어
# 있습니다. bash 는 스크립트를 읽어가며 실행하기 때문에 실행 중 파일이 바뀌면
# 엉뚱한 줄을 읽을 수 있어요. $DEST 안에서 실행됐다면 임시 복사본으로 재실행합니다.
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || true)"
case "${MJ_REEXEC:-}${SELF}" in
  "$DEST"/*)
    TMP_SELF="$(mktemp /tmp/mj-setup.XXXXXX.sh)"
    cp "$SELF" "$TMP_SELF"
    MJ_REEXEC=1 exec bash "$TMP_SELF" "$@"
    ;;
esac

if have apt-get; then PKG=apt
elif have dnf;   then PKG=dnf
elif have yum;   then PKG=yum
else PKG=""
fi

pkg_install() {  # 실패해도 스크립트를 죽이지 않음 — 호출부에서 판단
  case "$PKG" in
    apt) sudo apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    yum) sudo yum install -y "$@" ;;
    *)   return 1 ;;
  esac
}

# --- 0. 스왑 (램 작은 VM 에서 dnf/pip 가 OOM 나는 것 방지) -------------------
TOTAL_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 9999)
if [ "$TOTAL_MB" -lt 1800 ] && [ ! -e /swapfile ]; then
  echo "==> 램이 ${TOTAL_MB}MB 라 스왑 2GB 를 먼저 만들어요"
  {
    sudo fallocate -l 2G /swapfile 2>/dev/null \
      || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  } || echo "!! 스왑 생성을 건너뛰었어요 (계속 진행합니다)"
fi

# --- 1. 파이썬 (있으면 그대로 사용) -----------------------------------------
PY=""
for c in python3.12 python3.11 python3.10 python3.9 python3; do
  have "$c" && PY="$c" && break
done
if [ -z "$PY" ]; then
  echo "==> 파이썬 설치"
  [ "$PKG" = apt ] && sudo apt-get update -y
  pkg_install python3 python3-venv python3-pip || {
    echo "!! 파이썬 설치 실패 — python3 를 직접 설치한 뒤 다시 실행하세요."; exit 1; }
  PY=python3
fi
echo "==> 파이썬: $($PY --version 2>&1)"

# --- 2. 코드 받기 (git 없으면 tarball 로) ------------------------------------
echo "==> 코드 받기 ($BRANCH)"
sudo mkdir -p "$DEST"
sudo chown "$BOT_USER":"$BOT_USER" "$DEST"
if [ -d "$DEST/.git" ] && have git; then
  git -C "$DEST" fetch origin "$BRANCH"
  git -C "$DEST" checkout "$BRANCH"
  git -C "$DEST" pull --ff-only origin "$BRANCH"
elif have git; then
  git clone --branch "$BRANCH" "$REPO" "$DEST"
else
  # git 이 없으면 굳이 설치하지 않고 tarball 로 받습니다 (패키지 설치 회피)
  echo "    git 이 없어서 tarball 로 받아요"
  curl -fsSL "$TARBALL" | tar xz -C "$DEST" --strip-components=1
fi

# --- 3. 가상환경 & 의존성 ----------------------------------------------------
echo "==> 가상환경 & 의존성 ($PY)"
if ! "$PY" -m venv "$DEST/venv" 2>/dev/null; then
  echo "    venv 모듈이 없어 설치를 시도해요"
  pkg_install "${PY}-devel" "${PY}-pip" || pkg_install python3-venv python3-pip || true
  "$PY" -m venv "$DEST/venv"
fi
# --no-cache-dir: 램/디스크가 빠듯한 VM 에서 pip 캐시가 OOM 을 유발하는 걸 막아요
"$DEST/venv/bin/pip" install --no-cache-dir --upgrade pip
"$DEST/venv/bin/pip" install --no-cache-dir -r "$DEST/requirements.txt"

# --- 4. .env ----------------------------------------------------------------
echo "==> .env 준비"
if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  echo "!! $DEST/.env 에 DISCORD_TOKEN 을 넣어주세요."
fi

# --- 5. systemd -------------------------------------------------------------
echo "==> systemd 서비스 등록"
sudo sed "s/^User=.*/User=$BOT_USER/" "$DEST/deploy/mahjong-bot.service" \
  | sudo tee /etc/systemd/system/mahjong-bot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable mahjong-bot

# --- 6. ffmpeg (선택 · 맨 마지막) -------------------------------------------
# 여기서 실패하거나 오래 걸려도 봇은 이미 설치가 끝난 상태입니다.
if have ffmpeg; then
  echo "==> ffmpeg 이미 있음 — 효과음 사용 가능"
elif [ -n "${WITH_FFMPEG:-}" ]; then
  echo "==> ffmpeg 설치 시도 — 오래 걸리면 Ctrl+C 로 건너뛰어도 됩니다"
  if [ "$PKG" = apt ]; then
    sudo apt-get install -y ffmpeg || echo "!! ffmpeg 건너뜀"
  else
    { pkg_install oracle-epel-release-el9 || pkg_install epel-release || true; } >/dev/null 2>&1
    pkg_install ffmpeg-free || pkg_install ffmpeg \
      || echo "!! ffmpeg 건너뜀 — 효과음만 비활성화되고 게임은 정상 동작해요."
  fi
else
  echo "==> ffmpeg 없음 — 효과음 없이 진행 (원하면 나중에 WITH_FFMPEG=1 로 설치)"
fi

cat <<EOF

✅ 설치 끝!

다음 두 가지만 하면 돼요:
  1) 토큰 입력:   nano $DEST/.env       (DISCORD_TOKEN=... 붙여넣기)
  2) 봇 시작:     sudo systemctl start mahjong-bot

상태 보기:   systemctl status mahjong-bot
로그 보기:   journalctl -u mahjong-bot -f
업데이트:    bash $DEST/deploy/setup.sh && sudo systemctl restart mahjong-bot
EOF
