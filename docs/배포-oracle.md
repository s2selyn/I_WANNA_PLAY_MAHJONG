# ☁️ 오라클 클라우드(무료) 배포 가이드

오라클 **Always Free** VM에서 마작 봇을 24/7 돌리는 방법이에요.
진짜 평생 무료(카드 등록은 필요)라 지인 봇 상시 운영에 딱이에요.

> 봇은 **바깥으로 나가는 연결만** 하고 들어오는 포트는 안 열어요.
> 그래서 방화벽/포트 개방 설정이 **필요 없어요.**

---

## 1. VM 만들기 (오라클 콘솔)

1. https://cloud.oracle.com 가입/로그인 (카드 등록 필요, **Always Free는 과금 안 됨**)
2. **Compute → Instances → Create Instance**
3. 이미지: **Canonical Ubuntu** (예: 22.04)
4. Shape (둘 중 아무거나, 둘 다 Always Free):
   - **VM.Standard.A1.Flex** (Ampere ARM) — 넉넉함. 이 봇엔 1 OCPU / 1~2GB면 충분
   - **VM.Standard.E2.1.Micro** (x86) — 1GB, 이 봇엔 이것도 OK
5. **SSH 키**: 콘솔에서 "Generate a key pair for me" 로 **private key 저장** (또는 내 공개키 붙여넣기)
6. **Create** → 인스턴스의 **Public IP** 확인

---

## 2. SSH 접속

```bash
# 다운받은 키 권한 (한 번만)
chmod 600 ~/Downloads/ssh-key-*.key

# Ubuntu 이미지의 기본 유저는 ubuntu
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<Public-IP>
```

> 📱 폰만 있다면 **Termius** 같은 SSH 앱으로도 접속 가능해요.

---

## 3. 한 방 설치 (스크립트)

VM 안에서 아래 한 줄이면 끝:

```bash
curl -fsSL https://raw.githubusercontent.com/s2selyn/I_WANNA_PLAY_MAHJONG/main/deploy/setup.sh | bash
```

스크립트가 자동으로:
- python·git 설치 → 코드 clone → 가상환경 & 의존성 설치
- `.env` 생성 → **systemd 서비스 등록 + 부팅 시 자동 시작(enable)**

---

## 4. 토큰 넣고 시작

```bash
nano /opt/mahjong-bot/.env
# DISCORD_TOKEN=여기에_봇_토큰   (저장: Ctrl+O, Enter / 종료: Ctrl+X)

sudo systemctl start mahjong-bot
```

확인:
```bash
systemctl status mahjong-bot     # active(running) 이면 성공
journalctl -u mahjong-bot -f     # 실시간 로그 (Logged in as ... 뜨면 OK)
```

이제 디스코드 채널에서 **`!mj`** → 로비가 뜨면 배포 완료! 🀄
VM은 계속 켜져 있으니 봇도 **24/7** 살아있어요. (재부팅해도 자동 시작)

---

## 🔧 운영 명령어

```bash
sudo systemctl restart mahjong-bot   # 재시작
sudo systemctl stop mahjong-bot      # 정지
sudo systemctl disable mahjong-bot   # 자동시작 끄기
journalctl -u mahjong-bot -f         # 로그 따라보기
```

### 코드 업데이트(새 버전 반영)
```bash
bash /opt/mahjong-bot/deploy/setup.sh
sudo systemctl restart mahjong-bot
```

---

## 🩹 문제 해결

- **`!mj` 무반응** → 디스코드 개발자 포털에서 **MESSAGE CONTENT INTENT** 켰는지 확인
- **로그에 `Improper token`** → `.env` 의 토큰 오타/공백 확인 후 `restart`
- **서비스가 안 뜸** → `journalctl -u mahjong-bot -e` 로 에러 확인.
  User 값이 안 맞으면(Oracle Linux면 `opc`) `/etc/systemd/system/mahjong-bot.service`
  의 `User=` 를 고치고 `sudo systemctl daemon-reload && sudo systemctl restart mahjong-bot`
- **DM 방식 손패가 안 옴** → 플레이어가 서버 DM 허용을 켜야 함(채널/모바일 방식은 불필요)

---

## 💡 참고

- 이 봇은 게임 상태를 **메모리에만** 두기 때문에 DB가 필요 없어요.
  재시작하면 진행 중이던 판은 사라지지만, 새로 `!mj` 하면 됩니다.
- 다른 브랜치를 배포하려면 `BRANCH=브랜치명 bash /opt/mahjong-bot/deploy/setup.sh` 처럼 쓰면 돼요.
