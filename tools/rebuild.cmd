@echo off
chcp 65001 >nul
rem ============================================================
rem  rebuild.cmd - 重新打包 BoltManager.exe
rem
rem  前置条件：
rem    1) PATH 里的 python 已安装 hidapi，且 solaar/logitech_receiver 已打补丁
rem       （用 tools\apply_patches.cmd 把补丁还回 site-packages）
rem    2) 已安装 pyinstaller：python -m pip install pyinstaller
rem
rem  产物：dist\BoltManager\BoltManager.exe
rem        这是 one-folder 形式，必须连同同目录的 _internal 一起分发
rem ============================================================
setlocal

rem 切到项目根目录（本脚本位于 tools\）
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未在 PATH 中找到 python
    exit /b 1
)

for /f "delims=" %%i in ('python -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%i"
echo Python: %PYVER%

python -m PyInstaller --noconfirm --clean ^
  --distpath "%CD%\dist" --workpath "%CD%\build" ^
  "%CD%\src\BoltManager.spec"

if errorlevel 1 (
    echo [ERROR] 打包失败
    exit /b 1
)

echo.
echo [OK] %CD%\dist\BoltManager\BoltManager.exe
endlocal
