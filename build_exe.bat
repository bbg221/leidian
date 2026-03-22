@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo 使用 Python: %PY%
"%PY%" -m pip install -q -r requirements.txt -r requirements-build.txt
if errorlevel 1 exit /b 1

echo.
echo 正在打包（单文件 exe，无控制台窗口）...
echo 若运行缺 DLL，可改脚本加上: --collect-all pygame （体积会大很多）
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name leidian ^
  --add-data "assets;assets" ^
  main.py

if errorlevel 1 (
  echo 打包失败。
  exit /b 1
)

echo.
echo 完成: dist\leidian.exe
echo.
echo 若 Win11 「智能应用控制」拦截 exe：需「正式代码签名证书」.pfx + scripts\sign_leidian.ps1
echo 或运行 sign_after_build.bat；自签名仍无法过 SAC。也可 run_game.bat 用源码运行。
endlocal
exit /b 0
