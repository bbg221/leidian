@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 1^) 自签名（仅本机测试，不能过智能应用控制）
echo 2^) 使用正式 .pfx（需已安装 Windows SDK 的 signtool）
echo.
choice /c 12 /n /m "请选择 [1/2]: "
if errorlevel 2 goto pfx
if errorlevel 1 goto self

:self
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sign_leidian.ps1" -SelfSigned
goto end

:pfx
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sign_leidian.ps1"
goto end

:end
pause
