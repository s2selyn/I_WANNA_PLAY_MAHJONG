#!/usr/bin/env bash
#
# 자동 배포(주기적 확인)를 설치합니다. VM 에서 한 번만 실행하세요:
#
#   bash /opt/mahjong-bot/deploy/install-auto-update.sh
#
# 주기를 바꾸려면 SCHEDULE 을 주세요 (systemd OnCalendar 형식 · **UTC 기준**):
#
#   SCHEDULE='*-*-* 20:00:00'   기본값 — 매일 20:00 UTC = 한국 시간 새벽 5시
#   SCHEDULE='*-*-* 03,15:00:00'  하루 두 번 (12시간 간격)
#   SCHEDULE='hourly'             매시 정각
#   SCHEDULE='Mon *-*-* 20:00:00' 매주 월요일
#
# 끄고 싶으면:  sudo systemctl disable --now mahjong-update.timer
#
set -euo pipefail

DEST="${DEST:-/opt/mahjong-bot}"
SCHEDULE="${SCHEDULE:-*-*-* 20:00:00}"
RUN_USER="${SUDO_USER:-$USER}"

sudo tee /etc/systemd/system/mahjong-update.service >/dev/null <<EOF
[Unit]
Description=Update the mahjong bot if a new commit is available
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# root 로 실행해요 — 서비스에는 TTY 가 없어서 내부 sudo 가 막힐 수 있거든요.
# 봇 자체는 BOT_USER 계정으로 계속 돌아갑니다.
Environment=BOT_USER=$RUN_USER
ExecStart=/usr/bin/env bash $DEST/deploy/auto-update.sh
EOF

sudo tee /etc/systemd/system/mahjong-update.timer >/dev/null <<EOF
[Unit]
Description=Check for mahjong bot updates on a schedule

[Timer]
OnCalendar=$SCHEDULE
# 꺼져 있던 동안 지나간 일정은 부팅 후 한 번 따라잡아요
Persistent=true
# 여러 대가 동시에 GitHub 를 두드리지 않도록 살짝 흩뜨립니다
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now mahjong-update.timer

cat <<EOF

✅ 자동 배포 설치 완료!

  주기       : $SCHEDULE  (UTC 기준)
  다음 실행  : $(systemctl show mahjong-update.timer -p NextElapseUSecRealtime --value 2>/dev/null || echo '-')

수동으로 즉시 배포:   sudo systemctl start mahjong-update
                      (또는) bash $DEST/deploy/setup.sh && sudo systemctl restart mahjong-bot
기록 보기:            journalctl -u mahjong-update -n 30 --no-pager
주기 변경:            SCHEDULE='*-*-* 09:00:00' bash $DEST/deploy/install-auto-update.sh
끄기:                 sudo systemctl disable --now mahjong-update.timer
EOF
