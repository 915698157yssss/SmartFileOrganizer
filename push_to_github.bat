@echo off
chcp 65001 >nul
title 推送到 GitHub

echo ==========================================
echo   ☁️  推送到 GitHub
echo ==========================================
echo.
echo 请确保已登录 GitHub，如果未登录：
echo   1. 打开 GitHub Desktop
echo   2. 登录你的 GitHub 账号
echo   3. 关掉后回到这里继续
echo.
pause
echo.

cd /d F:\SmartFileOrganizer

echo [1/3] 检查登录状态...
gh auth status >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠ 未登录，正在启动浏览器登录...
    echo   请在浏览器中完成登录后回到此窗口
    gh auth login --web
    if %errorlevel% neq 0 (
        echo ❌ 登录失败
        pause
        exit /b 1
    )
)
echo ✔ 已登录 GitHub
echo.

echo [2/3] 创建私有仓库并推送...
gh repo create SmartFileOrganizer ^
    --private ^
    --description "智能文件分类器" ^
    --push ^
    --source F:\SmartFileOrganizer ^
    --remote origin

if %errorlevel% neq 0 (
    echo.
    echo ⚠ 仓库可能已存在，尝试直接推送...
    git push -u origin master
)

if %errorlevel% equ 0 (
    echo ✔ 推送成功！
) else (
    echo ❌ 推送失败
    pause
    exit /b 1
)
echo.

echo [3/3] 清理旧版本文件...
echo 保留: main.py (v6.2 最新版本)
echo 删除旧版本源文件...
del /q F:\SmartFileOrganizer\smart_organizer_gui*.py 2>nul
del /q F:\SmartFileOrganizer\smart_organizer_v3lite*.py 2>nul
del /q F:\SmartFileOrganizer\test.py 2>nul
del /q F:\SmartFileOrganizer\SmartFileOrganizer.py 2>nul

echo.
echo ==========================================
echo   🎉 全部完成！
echo ==========================================
echo.
echo   GitHub 仓库（私密）:
echo   https://github.com/915698157yssss/SmartFileOrganizer
echo.
echo   本地保留文件:
echo   F:\SmartFileOrganizer\main.py  ← v6.2 最新版本
echo.
echo   版本历史 (7个版本):
echo     v1.0 → v2.0 → v3.0 → v4.0 → v5.0 → v6.0 → v6.1 → v6.2
echo.
echo   用 git log 查看完整历史：
echo     cd F:\SmartFileOrganizer ^&^& git log --oneline
echo.
pause
