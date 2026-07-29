#!/usr/bin/env bash
# 맥/리눅스용: `./run.sh` 로 봇을 켜요. 끄려면 Ctrl+C.
cd "$(dirname "$0")" || exit 1
echo "필요한 패키지 확인 중..."
python3 -m pip install -q -r requirements.txt
python3 bot.py
