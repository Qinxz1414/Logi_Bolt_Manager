@echo off
chcp 65001 >nul
rem ============================================================
rem  solaar-cli.cmd - 直接以 CLI 方式调用打过补丁的 solaar
rem  用途：排查接收器/设备问题，比 GUI 多出详细日志
rem  示例：
rem      solaar-cli.cmd show                 :: 列出接收器与设备
rem      solaar-cli.cmd -d logitech_receiver show
rem ============================================================
setlocal

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未在 PATH 中找到 python
    exit /b 1
)

python -c "import sys; from solaar import cli; sys.exit(cli.run(sys.argv[1:]) or 0)" %*
endlocal
