#!/usr/bin/env bash
#
# 램이 아주 작은 무료 VM(0.5~1GB)에서 ffmpeg 를 설치하기 위한 스크립트.
#
#   bash /opt/mahjong-bot/deploy/install-ffmpeg.sh
#
# 그냥 `dnf install ffmpeg-free` 를 하면 dnf 가 모든 저장소 메타데이터를 메모리에
# 올리고 권장(weak) 의존성까지 끌어와서 OOM 으로 죽습니다. 여기서는
#   1) 봇을 잠깐 멈춰 메모리를 확보하고
#   2) 필요한 저장소만 켜고
#   3) weak dependency 를 제외해서
# 최대 사용량을 크게 낮춥니다. 끝나면 봇을 다시 켜요.
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
if systemctl is-active --quiet "$BOT_UNIT"; then
  BOT_WAS_ACTIVE=yes
  echo "==> 설치 동안 봇을 잠시 멈춥니다 (메모리 확보)"
  sudo systemctl stop "$BOT_UNIT"
fi

restore_bot() {
  if [ "$BOT_WAS_ACTIVE" = yes ]; then
    echo "==> 봇을 다시 시작합니다"
    sudo systemctl start "$BOT_UNIT"
  fi
}
trap restore_bot EXIT

echo "==> dnf 캐시 정리"
sudo dnf clean all >/dev/null 2>&1 || true

# EPEL 저장소 id 찾기 (배포판마다 이름이 달라요: ol9_developer_EPEL, epel ...)
EPEL_ID="$(sudo dnf repolist --all 2>/dev/null \
  | awk 'tolower($1) ~ /epel/ {print $1; exit}')"

if [ -z "$EPEL_ID" ]; then
  echo "==> EPEL 저장소가 없어 먼저 설치합니다"
  sudo dnf install -y --setopt=install_weak_deps=False \
    oracle-epel-release-el9 || sudo dnf install -y epel-release || {
      echo "!! EPEL 설치 실패 — 효과음 없이 사용하셔도 게임은 정상입니다."; exit 1; }
  EPEL_ID="$(sudo dnf repolist --all 2>/dev/null \
    | awk 'tolower($1) ~ /epel/ {print $1; exit}')"
fi
echo "==> EPEL 저장소: ${EPEL_ID:-(찾지 못함)}"

echo "==> ffmpeg 설치 (필요한 저장소만 + weak deps 제외)"
REPOS="ol9_baseos_latest,ol9_appstream,${EPEL_ID}"
if sudo dnf install -y \
      --disablerepo='*' --enablerepo="$REPOS" \
      --setopt=install_weak_deps=False \
      ffmpeg-free; then
  echo "✅ 설치 성공"
elif sudo dnf install -y --setopt=install_weak_deps=False ffmpeg-free; then
  echo "✅ 설치 성공 (전체 저장소 사용)"
else
  echo "!! ffmpeg 설치 실패 — 효과음만 비활성화되고 게임은 정상 동작해요."
  exit 1
fi

ffmpeg -version | head -1 || true
echo
echo "효과음을 쓰려면 봇 재시작 없이 바로 사용할 수 있어요:"
echo "  !mj sound riichi   (음성 파일 첨부)"
