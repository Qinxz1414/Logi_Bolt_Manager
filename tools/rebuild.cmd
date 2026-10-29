@echo off
chcp 65001 >nul
rem ============================================================
rem  rebuild.cmd - 重新打包 BoltManager.exe
rem
rem  前置条件：
rem    1) PATH 里的 python 已装 requirements-dev.txt 里的依赖
rem    2) 补丁已应用：tools\apply_patches.cmd
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

rem hidapi.dll 不在 pip 包里，缺了就先从官方 release 下载
if not exist "build\hidapi\hidapi.dll" (
    python "tools\fetch_hidapi.py"
    if errorlevel 1 exit /b 1
)

rem 构建前先确认补丁在位，避免打出一个读不到设备的 exe
python "tools\apply_patches.py" --check >nul 2>&1
if errorlevel 1 (
    echo [WARN] 补丁可能未应用，建议先执行：tools\apply_patches.cmd
)

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
