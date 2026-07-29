# 🔊 효과음 폴더

여기에 오디오 파일을 넣으면 봇이 **음성 채널에서** 해당 순간에 재생해요.
파일이 **없으면 그냥 조용히 넘어가요** (있는 것만 재생).

## 파일 이름 규칙

아래 이름으로 넣으면 자동 인식돼요. 확장자는 `.mp3`, `.ogg`, `.wav`, `.m4a` 중 아무거나:

| 파일명 | 재생 순간 |
|---|---|
| `riichi` | 리치 선언 🎏 |
| `ron` | 론 화료 🀄 |
| `tsumo` | 쯔모 화료 |
| `pon` | 퐁 콜 |
| `chi` | 치 콜 |
| `kan` | 깡 콜 |

예: `riichi.mp3`, `ron.wav` …

## 어떻게 동작하나요?

- `!mj` 를 친 사람이 **음성 채널에 들어가 있으면** 봇이 그 채널에 자동 입장해요.
- 위 순간이 오면 해당 효과음을 재생해요 (여러 개가 겹치면 순서대로).
- 대국이 끝나거나 로비를 취소하면 봇이 음성에서 자동 퇴장해요.

## 효과음은 어디서 구하나요?

저작권 문제로 기본 제공은 안 해요. 무료 효과음 사이트에서 받아서 넣으세요:

- [freesound.org](https://freesound.org) (CC 라이선스)
- [pixabay.com/sound-effects](https://pixabay.com/sound-effects/)
- [mixkit.co/free-sound-effects](https://mixkit.co/free-sound-effects/)

> 짧은 효과음(1~3초) 권장. 파일이 너무 길면 다음 효과음이 밀려요.

## 필요 조건

- 서버(호스트)에 **FFmpeg** 설치 (오라클 setup.sh 는 자동 설치)
- `pip install -r requirements.txt` (PyNaCl 포함)
- 봇에게 음성 채널 **연결(Connect) + 말하기(Speak)** 권한
