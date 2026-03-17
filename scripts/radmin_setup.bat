@echo off

:: DNS который будем устанавливать
set DNS_ALT=26.240.80.131

:: Проверка прав администратора
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Administrator privileges required. Restarting...
    powershell -Command "Start-Process '%~f0' -Verb runAs"
    exit
)

echo Adding Alternate DNS for Radmin VPN...

netsh interface ip add dns name="Radmin VPN" %DNS_ALT% index=2

ipconfig /flushdns

echo.
echo Alternate DNS set to: %DNS_ALT%
pause