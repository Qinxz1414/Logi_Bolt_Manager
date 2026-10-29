@echo off
chcp 65001 >nul
rem ============================================================
rem  apply_patches.cmd - 把 patches\ 里的补丁应用到当前 Python 的 site-packages
rem
rem  参数透传给 apply_patches.py：
rem      apply_patches.cmd            :: 应用
rem      apply_patches.cmd --check    :: 只检查
rem      apply_patches.cmd --revert   :: 撤销
rem
rem  前置条件：python -m pip install solaar==1.1.20 hidapi
rem ============================================================
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未在 PATH 中找到 python
    exit /b 1
)

python "tools\apply_patches.py" %*
endlocal
