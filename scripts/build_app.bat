@echo off
chcp 65001 >nul
title 构建智能文件分类器 - 打包脚本 v6.2

echo ================================================
echo   智能文件分类器 v6.2 - 应用打包（优化版）
echo ================================================
echo.

:: 设置变量
set PROJECT_DIR=F:\SmartFileOrganizer
set SCRIPT_DIR=%PROJECT_DIR%\scripts
set BUILD_DIR=%PROJECT_DIR%\build
set DIST_DIR=%PROJECT_DIR%\dist
set APP_NAME=SmartFileOrganizer

echo [1/4] 复制模型文件到项目目录...
set MODEL_SRC=F:\Python\Cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2\snapshots\e8f8c211226b894fcb81acc59f3b34ba3efd5f42
set MODEL_DST=%BUILD_DIR%\model

if exist "%MODEL_DST%" rmdir /s /q "%MODEL_DST%"
xcopy "%MODEL_SRC%" "%MODEL_DST%\" /E /I /Q >nul
echo ✔ 模型文件已复制到 %MODEL_DST%
echo.

echo [2/4] 修改代码，使用本地模型路径...
python -c "
import re

with open(r'%PROJECT_DIR%\SmartFileOrganizer.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换模型加载路径为本地路径
old = '\"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2\"'
new = 'os.path.join(os.path.dirname(__file__), \"model\") if getattr(sys, \"frozen\", False) else \"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2\"'
content = content.replace(old, new)

# 添加sys导入（如果不存在）
if 'import sys' not in content:
    content = content.replace('import os', 'import os\nimport sys')

with open(r'%BUILD_DIR%\SmartFileOrganizer.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('代码已修改，已保存到 ' + r'%BUILD_DIR%\SmartFileOrganizer.py')
"
echo ✔ 代码修改完成
echo.

echo [3/4] 使用 PyInstaller 打包（优化版）...
cd /d "%BUILD_DIR%"

set UPX_DIR=%PROJECT_DIR%\scripts\upx\upx-4.2.4-win64

pyinstaller --onedir ^
    --name "%APP_NAME%" ^
    --windowed ^
    --upx-dir "%UPX_DIR%" ^
    --upx-exclude "*.vmp.exe" ^
    --add-data "model;model" ^
    --add-data "model\1_Pooling\config.json;model\1_Pooling" ^
    --add-data "model\config.json;model" ^
    --add-data "model\config_sentence_transformers.json;model" ^
    --add-data "model\modules.json;model" ^
    --add-data "model\sentence_bert_config.json;model" ^
    --add-data "model\special_tokens_map.json;model" ^
    --add-data "model\tokenizer.json;model" ^
    --add-data "model\tokenizer_config.json;model" ^
    --add-data "model\model.safetensors;model" ^
    --add-data "model\README.md;model" ^
    --hidden-import sentence_transformers ^
    --hidden-import faiss ^
    --hidden-import watchdog ^
    --hidden-import tkinter ^
    --hidden-import PIL ^
    --hidden-import numpy.core.multiarray ^
    --hidden-import numpy.core._methods ^
    --hidden-import transformers ^
    --hidden-import transformers.models.bert ^
    --hidden-import transformers.models.bert.configuration_bert ^
    --hidden-import transformers.models.bert.modeling_bert ^
    --hidden-import transformers.models.auto ^
    --hidden-import transformers.models.auto.configuration_auto ^
    --hidden-import transformers.models.auto.modeling_auto ^
    --hidden-import transformers.models.auto.feature_extraction_auto ^
    --hidden-import transformers.models.auto.tokenization_auto ^
    --hidden-import transformers.models.auto.processing_auto ^
    --exclude-module huggingface_hub ^
    --exclude-module torch.testing ^
    --exclude-module torch.distributions ^
    --exclude-module torch.onnx ^
    --clean ^
    SmartFileOrganizer.py

echo.

if exist "%BUILD_DIR%\dist\%APP_NAME%\%APP_NAME%.exe" (
    echo ✔ PyInstaller 打包成功！
    echo   输出路径: %BUILD_DIR%\dist\%APP_NAME%\
) else (
    echo ❌ PyInstaller 打包失败
    exit /b 1
)

echo.
echo [4/4] 整理输出目录...
:: UPX 后压缩 DLL
echo 应用 UPX 压缩 DLL...
"%UPX_DIR%\upx.exe" --best --lzma "%BUILD_DIR%\dist\%APP_NAME%\*.dll" 2>nul
if exist "%BUILD_DIR%\dist\%APP_NAME%\torch\lib\*.dll" (
    "%UPX_DIR%\upx.exe" --best --lzma "%BUILD_DIR%\dist\%APP_NAME%\torch\lib\*.dll" 2>nul
)

:: 复制到项目 dist 目录
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
mkdir "%DIST_DIR%"
xcopy "%BUILD_DIR%\dist\%APP_NAME%\" "%DIST_DIR%\%APP_NAME%\" /E /I /Q >nul
echo ✔ 已复制到 %DIST_DIR%\%APP_NAME%\
echo.

echo ================================================
echo   🎉 打包完成！
echo ================================================
echo.
echo 输出位置: %DIST_DIR%\%APP_NAME%\
echo 运行方式: 双击 %APP_NAME%.exe
echo 版本: v6.2（优化版 - 精简依赖 + UPX 压缩）
echo 总大小: 
dir /S "%DIST_DIR%\%APP_NAME%" | findstr "File(s)"
echo.
pause
