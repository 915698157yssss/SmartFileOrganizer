"""Build preparation: patch the source for packaging"""
import os
import sys

src_path = r'F:\SmartFileOrganizer\SmartFileOrganizer.py'
dst_path = r'F:\SmartFileOrganizer\build\SmartFileOrganizer.py'

with open(src_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 替换模型加载路径 - 打包后从本地 model/ 目录加载
old_model = '"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"'
new_model = (
    'os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__), "model")'
)
content = content.replace(old_model, new_model)

# 2. 确保 sys 被导入
if 'import sys' not in content.split('\n')[0:30]:
    content = content.replace('import os', 'import os\nimport sys', 1)

# 3. 清空默认路径，让用户在 GUI 中设置
content = content.replace(
    'DEFAULT_WATCH_FOLDER = r"F:\\SmartFileOrganizer\\待分类文件"',
    'DEFAULT_WATCH_FOLDER = ""'
)
content = content.replace(
    'DEFAULT_BASE_TARGET = r"G:\\（2026.6.5备份）眉山苏伊士污水处理有限公司"',
    'DEFAULT_BASE_TARGET = ""'
)

# 4. 数据库路径改为程序所在目录
content = content.replace(
    'os.path.join(os.path.expanduser("~"), "Documents", "SmartOrganizerData")',
    'os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_organizer_data")'
)

with open(dst_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Code patched -> {dst_path}")
print("Changes:")
print("  - Model path -> local ./model/ folder")
print("  - Default paths cleared")
print("  - DB location -> ./smart_organizer_data/")
print("  - Added sys import")
