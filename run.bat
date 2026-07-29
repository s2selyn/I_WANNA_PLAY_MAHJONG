@echo off
REM 윈도우용: 이 파일을 더블클릭하면 봇이 켜져요. 끄려면 이 창에서 Ctrl+C.
cd /d "%~dp0"
echo 필요한 패키지 확인 중...
python -m pip install -q -r requirements.txt
python bot.py
echo.
echo 봇이 종료되었습니다.
pause
