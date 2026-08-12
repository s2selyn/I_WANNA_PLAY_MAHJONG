#!/usr/bin/env bash
#
# 램이 아주 작은 무료 VM(0.5~1GB)에서 ffmpeg 를 설치하기 위한 스크립트.
#
#   bash /opt/mahjong-bot/deploy/install-ffmpeg.sh
#
# SSH 를 끊고 자리를 뜰 거라면 systemd 에 맡기세요 (창을 닫아도 끝까지 진행돼요):
#
#   sudo systemd-run --unit=mj-ffmpeg --setenv=STOP_BOT=1 \
#       bash /opt/mahjong-bot/deploy/install-ffmpeg.sh
#   systemctl status mj-ffmpeg      # 나중에 결과 확인
#
# 그냥 `dnf install ffmpeg-free` 를 하면 dnf 가 모든 저장소 메타데이터를 메모리에
# 올리고 권장(weak) 의존성까지 끌어와서 OOM 으로 죽습니다. 여기서는
#   1) 필요한 저장소만 켜고
#   2) weak dependency 를 제외해서
# 최대 사용량을 크게 낮춥니다.
#
# 환경변수:
#   STOP_BOT=1   설치 동안 봇을 잠시 멈춰 메모리를 더 확보합니다(끝나면 자동 재시작).
#                기본값은 **멈추지 않음** — 대국 중에도 돌릴 수 있게요.
#                다만 램이 빠듯하면 OOM 킬러가 봇을 잡아갈 수도 있어요
#                (systemd 가 곧바로 되살리지만 진행 중이던 판은 사라져요).
#
set -uo pipefail

BOT_UNIT=mahjong-bot

echo "==> 현재 메모리"
free -h

# 스왑이 꺼져 있으면 켜기 (재부팅 후 fstab 이 없을 때 대비)
if [ -e /swapfile ] && ! swapon --show | grep -q swapfile; then
  echo "==> 스왑을 다시 켭니다"
  sudo swapon /swapfile || true
fi

BOT_WAS_ACTIVE=no
if [ -n "${STOP_BOT:-}" ] && systemctl is-active --quiet "$BOT_UNIT"; then
  BOT_WAS_ACTIVE=yes
  echo "==> 설치 동안 봇을 잠시 멈춥니다 (메모리 확보)"
  sudo systemctl stop "$BOT_UNIT"
else
  echo "==> 봇은 계속 실행한 채로 설치합니다 (STOP_BOT=1 로 잠시 멈출 수 있어요)"
fi

restore_bot() {
  if [ "$BOT_WAS_ACTIVE" = yes ]; then
    echo "==> 봇을 다시 시작합니다"
    sudo systemctl start "$BOT_UNIT"
  fi
}
# SSH 가 끊겨(HUP) 중간에 죽더라도 봇은 반드시 다시 켜지도록
trap restore_bot EXIT INT TERM HUP

if command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v dnf >/dev/null 2>&1; then PKG=dnf
else PKG=""
fi

have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1 || [ -x /usr/local/bin/ffmpeg ]; }

# --- 1) 정적 바이너리 (가장 확실 · dnf 메타데이터를 안 써서 램도 거의 안 먹어요) ---
# Oracle Linux 의 EPEL 에는 ffmpeg 계열 패키지가 아예 없어서, 패키지 설치는
# 몇 분을 돌고도 "No match for argument" 로 끝나요. 그래서 이 방법을 먼저 씁니다.
install_static() {
  echo "==> 정적 ffmpeg 빌드를 내려받아요 (약 30MB)"
  command -v xz >/dev/null 2>&1 || sudo dnf install -y xz >/dev/null 2>&1 || true
  tmp="$(mktemp -d)"
  url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
  [ "$(uname -m)" = "aarch64" ] && \
    url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz"
  if ! curl -fL --retry 3 -o "$tmp/f.tar.xz" "$url"; then
    rm -rf "$tmp"; return 1
  fi
  tar xJf "$tmp/f.tar.xz" -C "$tmp" || { rm -rf "$tmp"; return 1; }
  d="$(find "$tmp" -maxdepth 1 -type d -name 'ffmpeg-*-static' | head -1)"
  [ -n "$d" ] || { rm -rf "$tmp"; return 1; }
  sudo install -m 755 "$d/ffmpeg"  /usr/local/bin/ffmpeg
  sudo install -m 755 "$d/ffprobe" /usr/local/bin/ffprobe 2>/dev/null || true
  rm -rf "$tmp"
  have_ffmpeg
}

# --- 2) 패키지 매니저 (Ubuntu 등) ---
install_pkg() {
  if [ "$PKG" = apt ]; then
    sudo apt-get update -y && sudo apt-get install -y ffmpeg
  else
    # RPM Fusion 에는 EL9 용 ffmpeg 가 있어요 (EPEL 에는 없음)
    sudo dnf install -y --nogpgcheck \
      "https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm" \
      >/dev/null 2>&1 || true
    sudo dnf install -y --setopt=install_weak_deps=False --allowerasing ffmpeg \
      || sudo dnf install -y --setopt=install_weak_deps=False ffmpeg-free
  fi
}

if install_static; then
  echo "✅ 정적 빌드 설치 성공"
elif install_pkg && have_ffmpeg; then
  echo "✅ 패키지로 설치 성공"
else
  echo "!! ffmpeg 설치 실패 — 효과음만 비활성화되고 게임은 정상 동작해요."
  exit 1
fi

ffmpeg -version 2>/dev/null | head -1 || /usr/local/bin/ffmpeg -version | head -1
echo
echo "효과음을 쓰려면 봇 재시작 없이 바로 사용할 수 있어요:"
echo "  !mj sound riichi   (음성 파일 첨부)"
