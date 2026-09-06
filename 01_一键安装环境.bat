@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "ROOT=%~dp0"
set "RUNTIME=%LOCALAPPDATA%\CommunityDID"
set "MINICONDA=%RUNTIME%\Miniconda3"
set "PYTHON=%MINICONDA%\python.exe"
set "INSTALLER=%TEMP%\CommunityDID_Miniconda3_py310.exe"
set "MIRROR_URL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-py310_24.7.1-0-Windows-x86_64.exe"
set "OFFICIAL_URL=https://repo.anaconda.com/miniconda/Miniconda3-py310_24.7.1-0-Windows-x86_64.exe"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

cls
echo ================================================================
echo          社区食堂 DID - 一键环境安装
echo ================================================================
echo.
echo 这个窗口会自动完成：
echo   1. 安装独立 Python 3.10 运行环境
echo   2. 使用国内 PyPI 镜像安装全部依赖
echo   3. 注册 Jupyter 内核 data_analyzer
echo   4. 尝试安装 7-Zip
echo   5. 自动检查环境
echo.
echo 请不要关闭本窗口。
echo.

if not exist "%RUNTIME%" mkdir "%RUNTIME%"

if exist "%PYTHON%" (
    echo [1/5] Python 环境已存在，跳过 Miniconda 安装。
) else (
    echo [1/5] 正在下载 Python 3.10 环境...
    echo       优先使用清华大学镜像。

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%MIRROR_URL%' -OutFile '%INSTALLER%'; exit 0 } catch { exit 1 }"

    if errorlevel 1 (
        echo [提示] 清华镜像下载失败，尝试官方备用地址...
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%OFFICIAL_URL%' -OutFile '%INSTALLER%'; exit 0 } catch { exit 1 }"
    )

    if errorlevel 1 goto :download_failed

    echo       下载完成，正在静默安装...
    start /wait "" "%INSTALLER%" /InstallationType=JustMe /AddToPath=0 /RegisterPython=0 /NoRegistry=1 /S /D=%MINICONDA%

    if not exist "%PYTHON%" goto :python_failed
)

echo.
echo [2/5] 正在安装 Python 依赖，请耐心等待...
"%PYTHON%" -m pip install --disable-pip-version-check --upgrade pip -i "%PIP_MIRROR%"
if errorlevel 1 goto :pip_failed

"%PYTHON%" -m pip install --disable-pip-version-check -r "%ROOT%requirements.txt" -i "%PIP_MIRROR%"
if errorlevel 1 goto :pip_failed

echo.
echo [3/5] 正在注册 Jupyter 内核 data_analyzer...
"%PYTHON%" -m ipykernel install --user --name data_analyzer --display-name "Python (data_analyzer)"
if errorlevel 1 goto :kernel_failed

echo.
echo [4/5] 检查 RAR 解压工具...
if exist "C:\Program Files\7-Zip\7z.exe" goto :sevenzip_ok
if exist "C:\Program Files (x86)\7-Zip\7z.exe" goto :sevenzip_ok

where winget >nul 2>nul
if errorlevel 1 (
    echo [WARN] 当前 Windows 没有 winget，无法自动安装 7-Zip。
    echo        Python 程序会先尝试 Windows 自带 tar.exe。
    echo        如果 RAR 无法解压，请手动安装 7-Zip 后再运行。
    goto :after_sevenzip
)

echo       未找到 7-Zip，正在通过 Windows Package Manager 安装...
winget install --id 7zip.7zip -e --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo [WARN] 7-Zip 自动安装没有成功。
    echo        Python 程序仍会尝试 Windows 自带 tar.exe。
) else (
    echo [OK]   7-Zip 安装完成。
)
goto :after_sevenzip

:sevenzip_ok
echo [OK]   已找到 7-Zip。

:after_sevenzip
echo.
echo [5/5] 正在检查环境...
"%PYTHON%" "%ROOT%check_env.py"
if errorlevel 1 goto :check_failed

echo.
echo ================================================================
echo                  环境安装成功
echo ================================================================
echo.
echo 下一步：
echo   Jupyter 版：双击 03_启动Jupyter.bat
echo   Python 版： 双击 04_运行Python版.bat
echo.
echo 原始 RAR 直接放在本文件夹根目录即可，名字不限。
echo ================================================================
pause
exit /b 0

:download_failed
echo.
echo [失败] Miniconda 下载失败。
echo 请检查电脑是否可以正常访问国内网站后重新双击本脚本。
goto :failed

:python_failed
echo.
echo [失败] Python 环境安装失败。
goto :failed

:pip_failed
echo.
echo [失败] Python 依赖安装失败。
echo 请保留当前窗口截图，方便排查具体失败包。
goto :failed

:kernel_failed
echo.
echo [失败] Jupyter 内核注册失败。
goto :failed

:check_failed
echo.
echo [失败] 环境自检没有通过。
goto :failed

:failed
echo.
echo ================================================================
echo 安装没有完成。请不要删除这个窗口中的错误信息。
echo ================================================================
pause
exit /b 1
