@echo off
setlocal
cd /d "%~dp0"

REM Tạo venv nếu chưa có
if not exist .venv (
  py -3.12 -m venv .venv
)
call .venv\Scripts\activate

python -m pip install -U pip wheel setuptools
python -m pip install PyInstaller PySide6 yt-dlp google-api-python-client google-auth-oauthlib cryptography certifi

pyinstaller ^
  --noconfirm --clean --windowed ^
  --name "YoutubeDownload" ^
  --icon "icon.ico" ^
  --collect-all PySide6 ^
  --collect-all yt_dlp ^
  --collect-all googleapiclient ^
  --collect-all google.oauth2 ^
  --collect-all google_auth_oauthlib ^
  --collect-all cryptography ^
  --add-binary "ffmpeg\ffmpeg.exe;." ^
  --add-data "icon.ico;." ^
  YoutubeDownload.py

echo Done. Dist in ".\dist\YoutubeDownload"
endlocal