"""Build version history from smart_organizer_gui v1 -> v6.1"""
import shutil, os, subprocess

repo = r'F:\SmartFileOrganizer'
os.chdir(repo)

versions = [
    ('smart_organizer_gui.py',   'v1.0 - 初始版本：基础智能文件分类器'),
    ('smart_organizer_gui2.py',  'v2.0 - 新增实时监控与自动分类'),
    ('smart_organizer_gui3.py',  'v3.0 - 优化UI界面，新增去重功能'),
    ('smart_organizer_gui4.py',  'v4.0 - 引入语义模型，增强智能分类'),
    ('smart_organizer_gui5.py',  'v5.0 - 新增训练系统和数据库管理'),
    ('smart_organizer_gui6.py',  'v6.0 - 重构架构，分离数据库管理器'),
    ('smart_organizer_gui6.1.py','v6.1 - 优化路径配置，修复Bug'),
]

for i, (src_file, msg) in enumerate(versions):
    # Copy version content to main file
    shutil.copy2(src_file, 'main.py')
    
    # Stage and commit
    subprocess.run(['git', 'add', 'main.py', '.gitignore'], capture_output=True)
    subprocess.run(['git', 'add', 'README.txt', 'requirements.txt'], capture_output=True)
    result = subprocess.run(['git', 'commit', '-m', msg], capture_output=True, text=True)
    print(f'[{i+1}/{len(versions)}] {msg}')
    if result.returncode != 0:
        print(f'  -> {result.stdout.strip()}')
        print(f'  -> {result.stderr.strip()}')

print('\nDone. Git log:')
subprocess.run(['git', 'log', '--oneline', '--all'])
