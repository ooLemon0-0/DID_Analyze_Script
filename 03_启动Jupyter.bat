@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%LOCALAPPDATA%\CommunityDID\Miniconda3\python.exe"

if not exist "%PYTHON%" (
    echo [失败] 尚未安装运行环境。
    echo 请先双击：01_一键安装环境.bat
    pause
    exit /b 1
)

set "NOTEBOOK="
for %%F in ("%~dp0*.ipynb") do (
    if not defined NOTEBOOK set "NOTEBOOK=%%~fF"
)

echo ================================================================
echo 正在启动 JupyterLab...
echo 项目目录：%~dp0
echo ================================================================
echo.

if defined NOTEBOOK (
    echo 将打开：%NOTEBOOK%
    "%PYTHON%" -m jupyter lab "%NOTEBOOK%"
) else (
    echo [提示] 根目录没有发现 .ipynb，打开项目目录。
    "%PYTHON%" -m jupyter lab --notebook-dir="%~dp0"
)

if errorlevel 1 (
    echo.
    echo [失败] JupyterLab 启动失败。
    pause
)
