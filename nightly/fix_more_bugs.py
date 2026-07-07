"""Apply additional fixes to main.py"""
import re

with open(r'F:\SmartFileOrganizer\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: None[:10] slice crash
content = content.replace(
    "db_stats.get('created_at', 'unknown')[:10]",
    "str(db_stats.get('created_at', '') or '')[:10] or 'unknown'"
)
content = content.replace(
    "db_stats.get('last_updated', 'unknown')[:10]",
    "str(db_stats.get('last_updated', '') or '')[:10] or 'unknown'"
)

# Fix 2: Add encoding fallback for load_hash_cache
old = '''    def load_hash_cache(self):
        if os.path.exists(self.hash_db_file):
            try:
                with open(self.hash_db_file, 'r', encoding='utf-8') as f:
                    self.hash_cache = json.load(f)
            except Exception:'''
new = '''    def load_hash_cache(self):
        if os.path.exists(self.hash_db_file):
            try:
                with open(self.hash_db_file, 'r', encoding='utf-8') as f:
                    self.hash_cache = json.load(f)
            except UnicodeDecodeError:
                try:
                    with open(self.hash_db_file, 'r', encoding='gbk') as f:
                        self.hash_cache = json.load(f)
                except Exception as e:
                    print(f"加载哈希缓存失败: {e}")
                    self.hash_cache = {}
            except Exception as e:
                print(f"加载哈希缓存失败: {e}")
                self.hash_cache = {}'''
content = content.replace(old, new)

# Fix 3: Add encoding fallback for load_duplicate_log
old = '''    def load_duplicate_log(self):
        if os.path.exists(self.duplicate_log_file):
            try:
                with open(self.duplicate_log_file, 'r', encoding='utf-8') as f:
                    self.duplicate_log = json.load(f)
            except Exception:'''
new = '''    def load_duplicate_log(self):
        if os.path.exists(self.duplicate_log_file):
            try:
                with open(self.duplicate_log_file, 'r', encoding='utf-8') as f:
                    self.duplicate_log = json.load(f)
            except UnicodeDecodeError:
                try:
                    with open(self.duplicate_log_file, 'r', encoding='gbk') as f:
                        self.duplicate_log = json.load(f)
                except Exception as e:
                    print(f"加载重复日志失败: {e}")
                    self.duplicate_log = []
            except Exception as e:
                print(f"加载重复日志失败: {e}")
                self.duplicate_log = []'''
content = content.replace(old, new)

with open(r'F:\SmartFileOrganizer\main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('All additional fixes applied')
print('Fixed: None[:10] slice, encoding fallback in load_hash_cache, load_duplicate_log')
