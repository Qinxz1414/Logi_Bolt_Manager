@echo off
chcp 65001 >nul
rem ============================================================
rem  apply_patches.cmd - 把 patches\ 里的补丁文件还原进当前 Python 的 site-packages
rem
rem  用途：迁移到新机器 / 重装 Python 后，一键恢复「已打补丁的 solaar」环境。
rem  覆盖目标（相对 site-packages）：
rem     hidapi\hidapi_impl.py
rem     logitech_receiver\base.py
rem     logitech_receiver\receiver.py
rem     logitech_receiver\desktop_notifications.py
rem
rem  注意：会覆盖上述文件，请确认已装好 solaar==1.1.20 与 hidapi。
rem ============================================================
setlocal

cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未在 PATH 中找到 python
    exit /b 1
)

for /f "delims=" %%i in ('python -c "import sysconfig;print(sysconfig.get_paths()[\"purelib\"])"') do set "SP=%%i"
if not exist "%SP%\solaar" (
    echo [ERROR] %SP%\solaar 不存在，请先执行：python -m pip install solaar==1.1.20 hidapi
    exit /b 1
)
echo site-packages: %SP%

xcopy /Y /I "patches\hidapi\hidapi_impl.py"              "%SP%\hidapi\"                        >nul
xcopy /Y /I "patches\logitech_receiver\base.py"          "%SP%\logitech_receiver\"             >nul
xcopy /Y /I "patches\logitech_receiver\receiver.py"      "%SP%\logitech_receiver\"             >nul
xcopy /Y /I "patches\logitech_receiver\desktop_notifications.py" "%SP%\logitech_receiver\"     >nul

if errorlevel 1 (
    echo [ERROR] 复制失败
    exit /b 1
)

echo.
echo [OK] 补丁已应用。验证：python -c "from logitech_receiver import base; print(hex(base.SOLAAR_SOFTWARE_ID))"
endlocal
