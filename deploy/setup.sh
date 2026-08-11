#!/usr/bin/env bash
#
# Oracle Cloud (Ubuntu) 한 방 설치 스크립트.
# VM 에 SSH 로 접속한 뒤 실행하세요:
#
#   curl -fsSL https://raw.githubusercontent.com/s2selyn/I_WANNA_PLAY_MAHJONG/main/deploy/setup.sh | bash
#
# 또는 레포를 이미 받았다면:  bash deploy/setup.sh
#
set -euo pipefail

REPO="https://github.com/s2selyn/I_WANNA_PLAY_MAHJONG.git"
BRANCH="${BRANCH:-main}"
DEST="/opt/mahjong-bot"   # 레포를 여기에 clone (레포 루트 = 봇 파일)

# 램이 작은 Always Free 인스턴스(E2.1.Micro 는 1GB)에서는 dnf/pip 도중 메모리가
# 바닥나 SSH 세션까지 먹통이 되는 일이 있어요. 무거운 작업 전에 스왑부터 깝니다.
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

echo "==> 패키지 설치 (python, venv, git, ffmpeg)"
# ffmpeg 는 음성 채널 효과음 재생에만 필요해요. 설치에 실패해도 봇은 정상 동작하고
# 효과음만 조용히 비활성화됩니다.
PY=python3
if command -v apt-get >/dev/null 2>&1; then
  # Debian / Ubuntu
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip git ffmpeg
elif command -v dnf >/dev/null 2>&1; then
  # Oracle Linux / RHEL / Rocky / Alma
  sudo dnf install -y git
  # 3.11 이 있으면 그걸 쓰고(권장), 없으면 배포판 기본 python3
  if sudo dnf install -y python3.11 python3.11-pip; then
    PY=python3.11
  else
    sudo dnf install -y python3 python3-pip
  fi
  echo "==> ffmpeg 설치 시도 (효과음용 · 실패해도 계속 진행돼요)"
  sudo dnf install -y oracle-epel-release-el9 \
    || sudo dnf install -y epel-release || true
  sudo dnf install -y ffmpeg-free || sudo dnf install -y ffmpeg \
    || echo "!! ffmpeg 를 건너뛰었어요 — 효과음만 비활성화되고 게임은 정상 동작해요."
else
  echo "!! 지원하지 않는 배포판이에요. python3 / git / ffmpeg 를 직접 설치한 뒤 다시 실행하세요."
  exit 1
fi

echo "==> 코드 받기 ($BRANCH)"
sudo mkdir -p "$DEST"
sudo chown "$USER":"$USER" "$DEST"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" fetch origin "$BRANCH"
  git -C "$DEST" checkout "$BRANCH"
  git -C "$DEST" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$DEST"
fi

echo "==> 가상환경 & 의존성 ($PY)"
"$PY" -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --upgrade pip
"$DEST/venv/bin/pip" install -r "$DEST/requirements.txt"

echo "==> .env 준비"
if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  echo "!! $DEST/.env 에 DISCORD_TOKEN 을 넣어주세요."
fi

echo "==> systemd 서비스 등록"
# 현재 리눅스 유저에 맞게 User= 를 치환해서 설치
sudo sed "s/^User=.*/User=$USER/" "$DEST/deploy/mahjong-bot.service" \
  | sudo tee /etc/systemd/system/mahjong-bot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable mahjong-bot

cat <<EOF

✅ 설치 끝!

다음 두 가지만 하면 돼요:
  1) 토큰 입력:   nano $DEST/.env       (DISCORD_TOKEN=... 붙여넣기)
  2) 봇 시작:     sudo systemctl start mahjong-bot

상태 보기:   systemctl status mahjong-bot
로그 보기:   journalctl -u mahjong-bot -f
업데이트:    bash $DEST/deploy/setup.sh && sudo systemctl restart mahjong-bot
EOF
