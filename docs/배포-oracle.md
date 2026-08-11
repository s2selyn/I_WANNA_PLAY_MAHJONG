# ☁️ 오라클 클라우드(무료) 배포 가이드

오라클 **Always Free** VM에서 마작 봇을 24/7 돌리는 방법이에요.
진짜 평생 무료(카드 등록은 필요)라 지인 봇 상시 운영에 딱이에요.

> 봇은 **바깥으로 나가는 연결만** 하고 들어오는 포트는 안 열어요.
> 그래서 방화벽/포트 개방 설정이 **필요 없어요.**

---

## 1. VM 만들기 (오라클 콘솔)

> 💻 이 단계는 **PC 브라우저**를 추천해요. 콘솔이 복잡해서 폰으로는 버거워요.
> (폰만 있다면 크롬 ⋮ → **데스크톱 사이트** 켜고 진행)

### 1-1. 가입

https://cloud.oracle.com 에서 **Start for free** → 가입.

- **카드 등록 필요** — 본인 확인용이고 **Always Free 자원만 쓰면 과금 안 돼요**
  (가입 후 30일 무료 크레딧이 끝나도 Always Free 자원은 계속 유지돼요)
- **홈 리전(Home Region)** 은 **나중에 못 바꿔요.** 한국이면 `South Korea Central (Seoul)`
  또는 `South Korea North (Chuncheon)` 선택

### 1-2. 인스턴스 생성

콘솔 좌측 **☰ 메뉴 → Compute → Instances → Create instance**

| 항목 | 선택할 것 |
|---|---|
| **Name** | 아무거나 (예: `mahjong-bot`) |
| **Image** | **Canonical Ubuntu** (22.04 또는 24.04) — `Edit` → `Change image` 에서 변경 |
| **Shape** | 아래 참고 |
| **Networking** | 기본값 그대로. **"Assign a public IPv4 address" 가 Yes** 인지만 확인 ⭐ |
| **SSH keys** | **Generate a key pair for me** → **Save private key** 로 키 파일 다운로드 ⭐ |

**Shape 고르기** (`Edit` → `Change shape`, 둘 다 Always Free):

- **VM.Standard.A1.Flex** (Ampere ARM) — **1 OCPU / 6GB** 정도로 잡으면 충분하고 넉넉해요
- **VM.Standard.E2.1.Micro** (x86, 1GB) — 이 봇엔 이것도 충분

> ⚠️ **"Out of host capacity" 에러**가 자주 떠요 (A1 인기가 많아서).
> 그럴 땐 ① 다른 **Availability Domain**(AD-1/2/3) 선택 ② 잠시 후 재시도
> ③ 그래도 안 되면 **E2.1.Micro** 로 만드세요. 이 봇은 1GB로도 잘 돌아가요.

**Create** 누르고 상태가 **RUNNING** 되면 완료 — 화면의 **Public IP address** 를 복사해두세요.

> 🔓 인바운드 포트는 **열 필요 없어요.** 봇은 밖으로만 연결하거든요.
> 보안 목록(Security List)이나 방화벽 건드리지 마세요.

---

## 2. SSH 접속

Ubuntu 이미지의 기본 유저는 **`ubuntu`** 예요.

**맥 / 리눅스**
```bash
chmod 600 ~/Downloads/ssh-key-*.key      # 키 권한 조이기 (한 번만)
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<Public-IP>
```

**윈도우 (PowerShell)** — `chmod` 는 리눅스 명령이라 **없어요. 그 줄은 건너뛰세요.**
```powershell
ssh -i C:\Users\<사용자명>\Downloads\ssh-key-2026-01-01.key ubuntu@<Public-IP>
```

처음 접속하면 `Are you sure you want to continue connecting?` → **yes** 입력.

- **폰**: **Termius** 같은 SSH 앱에 키 파일 넣어서 접속
- ❗ 윈도우에서 `UNPROTECTED PRIVATE KEY FILE` 이 뜨면 권한을 좁혀주세요:
  ```powershell
  icacls "<키경로>" /inheritance:r
  icacls "<키경로>" /grant:r "$env:USERNAME:(R)"
  ```
- ❗ `Permission denied` 면 → 키 경로/파일명 확인, 유저명이 `ubuntu` 인지 확인
  (Oracle Linux 이미지를 골랐다면 `opc`)

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
