@echo off
chcp 936 >nul 2>&1
cd /d "%~dp0"

echo ========================================
echo   城市温度预测系统 - 一键运行
echo ========================================
echo.

echo [1/3] 检查 Python 环境...
set "PY=python"
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 goto nopython
    set "PY=py"
)
%PY% --version

echo [2/3] 检查依赖库...
%PY% -c "import requests,pandas,numpy,statsmodels,sklearn" >nul 2>&1
if errorlevel 1 goto install
echo       依赖已齐全
goto run

:install
echo       缺少依赖，正在安装，请稍候...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [警告] 依赖安装失败
    echo        请手动执行下面这行命令:
    echo        %PY% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:run
echo [3/3] 采集数据并预测...
echo.
%PY% run_predict.py
if errorlevel 1 (
    echo.
    echo [错误] 运行失败，请查看上方的报错信息
    pause
    exit /b 1
)

echo.
echo ========================================
echo   运行完成！正在打开预测结果页面...
echo ========================================
start "" "%~dp0天气温度预测系统.html"
pause
exit /b 0

:nopython
echo.
echo [错误] 未检测到 Python
echo        请先安装 Python 3.9 或更高版本
echo        下载地址: https://www.python.org/downloads/
echo        安装时请勾选 Add Python to PATH
pause
exit /b 1
