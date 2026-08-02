@echo off
echo ================================================
echo   Build OBD2 Scanner GUI menjadi .exe (Windows)
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo Install Python 3.9+ dari https://www.python.org/downloads/
    echo Saat install, CENTANG "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/3] Install dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo.
echo [2/3] Build exe (bisa makan waktu 1-3 menit)...
python -m PyInstaller --clean obd2_scanner.spec

echo.
echo [3/3] Selesai!
if exist dist\OBD2Scanner.exe (
    echo Berhasil: dist\OBD2Scanner.exe
) else (
    echo [WARNING] File exe tidak ditemukan di dist\. Cek pesan error di atas.
)
pause
