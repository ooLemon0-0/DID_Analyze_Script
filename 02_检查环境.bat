@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%LOCALAPPDATA%\CommunityDID\Miniconda3\python.exe"

if not exist "%PYTHON%" (
    echo [失败] 尚未安装运行环境。
    echo 请先双击：01_一键安装环境.bat
    echo.
    pause
    exit /b 1
)

"%PYTHON%" "%~dp0check_env.py"
echo.
pause
