@echo off
chcp 65001 >nul
title 构建 SmartFileOrganizer v7
cd /d "%~dp0"

echo [1/3] 安装 PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 打包（onedir，启动快，便于替换/更新）...
python -m PyInstaller --noconfirm --clean --windowed --onedir ^
    --name SmartFileOrganizer ^
    main.py
if errorlevel 1 goto :fail

echo [3/3] 完成
echo 输出目录: %CD%\dist\SmartFileOrganizer\
echo 直接运行 dist\SmartFileOrganizer\SmartFileOrganizer.exe
pause
exit /b 0

:fail
echo.
echo ❌ 构建失败，请检查上方错误。
pause
exit /b 1
