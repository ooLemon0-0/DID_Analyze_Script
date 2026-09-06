@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%LOCALAPPDATA%\CommunityDID\Miniconda3\python.exe"

if not exist "%PYTHON%" (
    echo [失败] 尚未安装运行环境。
    echo 请先双击：01_一键安装环境.bat
    pause
    exit /b 1
)

if not exist "%~dp0run_analysis.py" (
    echo [失败] 根目录缺少 run_analysis.py
    pause
    exit /b 1
)

echo ================================================================
echo 社区食堂 DID - Python 版开始运行
echo ================================================================
echo.
echo 注意：原始数据很大，空间匹配和回归可能运行较长时间。
echo 只要窗口仍有新输出，就不要关闭。
echo.

"%PYTHON%" -u "%~dp0run_analysis.py"
set "ERR=%ERRORLEVEL%"

echo.
if "%ERR%"=="0" (
    echo ================================================================
    echo 分析运行完成。
    echo 结果位于：%~dp0processed
    echo ================================================================
) else (
    echo ================================================================
    echo [失败] 分析中途报错，错误码：%ERR%
    echo 请保留本窗口中的最后一段错误信息。
    echo ================================================================
)

echo.
pause
exit /b %ERR%
