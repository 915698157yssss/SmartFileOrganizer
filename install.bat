@echo off
chcp 65001 >nul
title 智能文件分类器 - 安装依赖

echo ==========================================
echo   🔧 智能文件分类器 v6 - 依赖安装脚本
echo ==========================================
echo.
echo 📦 正在安装依赖包...
echo.

:: 安装 watchdog (文件监听)
echo [1/3] 安装 watchdog...
pip install --no-index --find-links=F:\SmartFileOrganizer\packages\watchdog\ watchdog
if %errorlevel% neq 0 (
    echo ❌ watchdog 安装失败
    pause
    exit /b 1
)
echo ✔ watchdog 安装成功
echo.

:: 安装 faiss-cpu (向量检索)
echo [2/3] 安装 faiss-cpu...
pip install --no-index --find-links=F:\SmartFileOrganizer\packages\faiss-cpu\ faiss-cpu
if %errorlevel% neq 0 (
    echo ❌ faiss-cpu 安装失败
    pause
    exit /b 1
)
echo ✔ faiss-cpu 安装成功
echo.

:: 安装 sentence-transformers (语义模型 + torch)
echo [3/3] 安装 sentence-transformers (含 torch)...
echo ⏳ 这步需要几分钟，请耐心等待...
pip install --no-index --find-links=F:\SmartFileOrganizer\packages\sentence-transformers\ sentence-transformers
if %errorlevel% neq 0 (
    echo ❌ sentence-transformers 安装失败
    pause
    exit /b 1
)
echo ✔ sentence-transformers 安装成功
echo.

echo ==========================================
echo   🎉 所有依赖安装完成！
echo ==========================================
echo.
echo 现在可以运行程序了：
echo   双击 SmartFileOrganizer.py 或在命令行运行：
echo   python F:\SmartFileOrganizer\SmartFileOrganizer.py
echo.
echo ⚠ 首次运行会自动下载中文语义模型（约 80MB）
echo   请保持网络畅通
echo.
pause
