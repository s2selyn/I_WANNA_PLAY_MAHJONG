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

echo "==> 패키지 설치 (python, venv, git, ffmpeg)"
sudo apt-get update -y
# ffmpeg 는 음성 채널 효과음 재생에 필요 (효과음 안 쓰면 없어도 무방)
sudo apt-get install -y python3 python3-venv python3-pip git ffmpeg

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

echo "==> 가상환경 & 의존성"
python3 -m venv "$DEST/venv"
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
