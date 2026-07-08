"""
智能文件分类器 v6.2 - 完整整合版（带返回按钮）
"""

import os
import time
import shutil
import json
import threading
import queue
import hashlib
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
from datetime import datetime
from collections import defaultdict
import re

# ==================== 配置区域 ====================
DEFAULT_WATCH_FOLDER = r"F:\SmartFileOrganizer\待分类文件"
DEFAULT_BASE_TARGET = r"G:\（2026.6.5备份）眉山苏伊士污水处理有限公司"
MIN_CONFIDENCE_THRESHOLD = 0.5
MAX_LEARNING_ITEMS = 10000
DUPLICATE_CHECK_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
                              '.txt', '.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', 
                              '.zip', '.rar', '.7z', '.exe', '.msi', '.iso'}


# ==================== 独立数据库管理器 ====================
class DatabaseManager:
    """独立数据库管理器 - 负责所有数据的存储、读取、备份和迁移"""
    
    DB_VERSION = "6.2"
    
    def __init__(self, base_path=None):
        if base_path is None:
            self.base_path = os.path.join(os.path.expanduser("~"), "Documents", "SmartOrganizerData")
        else:
            self.base_path = base_path
        
        os.makedirs(self.base_path, exist_ok=True)
        
        self.db_file = os.path.join(self.base_path, "learning_data.db")
        self.db_backup_file = os.path.join(self.base_path, "learning_data_backup.db")
        self.db_export_file = os.path.join(self.base_path, "learning_data_export.json")
        self.hash_db_file = os.path.join(self.base_path, "file_hash.json")
        self.duplicate_log_file = os.path.join(self.base_path, "duplicate_log.json")
        self.config_file = os.path.join(self.base_path, "config.json")
        
        self.memory = []
        self.hash_cache = {}
        self.duplicate_log = []
        self.config = {}
        
        self.load_all()
    
    def load_all(self):
        self.load_config()
        self.load_learning_data()
        self.load_hash_cache()
        self.load_duplicate_log()
    
    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            except Exception:
                self.config = {}
        else:
            self.config = {
                'db_version': self.DB_VERSION,
                'created_at': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'total_learnings': 0,
                'total_duplicates': 0
            }
            self.save_config()
    
    def save_config(self):
        self.config['last_updated'] = datetime.now().isoformat()
        self.config['total_learnings'] = len(self.memory)
        self.config['total_duplicates'] = len(self.duplicate_log)
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def load_learning_data(self):
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get('version') == self.DB_VERSION:
                        self.memory = data.get('memory', [])
                    else:
                        self.memory = self._migrate_old_data(data)
            except Exception as e:
                print(f"加载学习数据失败: {e}")
                self.memory = []
        else:
            self.memory = []
        
        self.config['total_learnings'] = len(self.memory)
        self.save_config()
    
    def _migrate_old_data(self, old_data):
        if 'memory' in old_data:
            memory = old_data['memory']
            for item in memory:
                if 'learned_at' not in item:
                    item['learned_at'] = datetime.now().isoformat()
            return memory
        return []
    
    def save_learning_data(self):
        try:
            data = {
                'version': self.DB_VERSION,
                'memory': self.memory,
                'updated_at': datetime.now().isoformat(),
                'total_entries': len(self.memory)
            }
            
            if os.path.exists(self.db_file):
                try:
                    shutil.copy2(self.db_file, self.db_backup_file)
                except Exception:
                    pass
            
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.config['total_learnings'] = len(self.memory)
            self.save_config()
        except Exception as e:
            print(f"保存学习数据失败: {e}")
    
    def export_learning_data(self, export_path=None):
        if export_path is None:
            export_path = self.db_export_file
        
        try:
            export_data = {
                'version': self.DB_VERSION,
                'exported_at': datetime.now().isoformat(),
                'memory': self.memory,
                'config': self.config,
                'total_entries': len(self.memory)
            }
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            return export_path
        except Exception as e:
            print(f"导出数据失败: {e}")
            return None
    
    def import_learning_data(self, import_path):
        try:
            with open(import_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
            
            if 'memory' in import_data:
                new_memory = import_data['memory']
            else:
                new_memory = import_data if isinstance(import_data, list) else []
            
            if not new_memory:
                return False, "未找到有效数据"
            
            valid_memory = []
            for item in new_memory:
                if 'text' in item and 'path' in item:
                    valid_memory.append(item)
            
            if not valid_memory:
                return False, "数据格式无效"
            
            # 合并数据
            existing_set = {(item['text'], item['path']) for item in self.memory}
            for item in valid_memory:
                key = (item['text'], item['path'])
                if key not in existing_set:
                    self.memory.append(item)
                    existing_set.add(key)
            self.memory = self.memory[:MAX_LEARNING_ITEMS]
            
            self.save_learning_data()
            return True, f"成功导入 {len(valid_memory)} 条数据"
        except Exception as e:
            return False, f"导入失败: {str(e)}"
    
    def load_hash_cache(self):
        if os.path.exists(self.hash_db_file):
            for enc in ['utf-8', 'gbk', 'gb2312', 'cp1252']:
                try:
                    with open(self.hash_db_file, 'r', encoding=enc) as f:
                        self.hash_cache = json.load(f)
                    break
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    print(f"尝试编码 {enc} 加载哈希缓存失败: {e}")
                    self.hash_cache = {}
                except Exception as e:
                    print(f"加载哈希缓存失败: {e}")
                    self.hash_cache = {}
                    break
        else:
            self.hash_cache = {}
    
    def save_hash_cache(self):
        try:
            with open(self.hash_db_file, 'w', encoding='utf-8') as f:
                json.dump(self.hash_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存哈希缓存失败: {e}")
    
    def load_duplicate_log(self):
        if os.path.exists(self.duplicate_log_file):
            for enc in ['utf-8', 'gbk', 'gb2312', 'cp1252']:
                try:
                    with open(self.duplicate_log_file, 'r', encoding=enc) as f:
                        self.duplicate_log = json.load(f)
                    break
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    print(f"尝试编码 {enc} 加载重复日志失败: {e}")
                    self.duplicate_log = []
                except Exception as e:
                    print(f"加载重复日志失败: {e}")
                    self.duplicate_log = []
                    break
        else:
            self.duplicate_log = []
    
    def save_duplicate_log(self):
        try:
            recent_log = self.duplicate_log[-1000:]
            with open(self.duplicate_log_file, 'w', encoding='utf-8') as f:
                json.dump(recent_log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存重复日志失败: {e}")
    
    def create_backup(self):
        backup_folder = os.path.join(self.base_path, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(backup_folder, exist_ok=True)
        
        try:
            for file_path in [self.db_file, self.hash_db_file, self.duplicate_log_file, self.config_file]:
                if os.path.exists(file_path):
                    shutil.copy2(file_path, os.path.join(backup_folder, os.path.basename(file_path)))
            return backup_folder
        except Exception as e:
            print(f"备份失败: {e}")
            return None
    
    def get_stats(self):
        return {
            'database_path': self.base_path,
            'db_version': self.DB_VERSION,
            'total_learnings': len(self.memory),
            'total_duplicates': len(self.duplicate_log),
            'hash_cache_size': len(self.hash_cache),
            'created_at': self.config.get('created_at', 'unknown'),
            'last_updated': self.config.get('last_updated', 'unknown')
        }
    
    def clear_all_data(self):
        self.memory = []
        self.hash_cache = {}
        self.duplicate_log = []
        self.save_learning_data()
        self.save_hash_cache()
        self.save_duplicate_log()
        return True


# ==================== 文件去重工具 ====================
class FileDeduplicator:
    def __init__(self, watch_folder, db_manager):
        self.watch_folder = watch_folder
        self.db_manager = db_manager
        parent_folder = os.path.dirname(watch_folder)
        self.duplicate_folder = os.path.join(parent_folder, "重复文件")
        os.makedirs(self.duplicate_folder, exist_ok=True)
    
    def get_file_hash(self, filepath, chunk_size=8192):
        if filepath in self.db_manager.hash_cache:
            return self.db_manager.hash_cache[filepath]
        
        try:
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                return None
            
            hasher = hashlib.sha256()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            hash_value = hasher.hexdigest()
            self.db_manager.hash_cache[filepath] = hash_value
            self.db_manager.save_hash_cache()
            return hash_value
        except Exception as e:
            print(f"计算哈希失败 {filepath}: {e}")
            return None
    
    def check_and_handle_duplicate(self, filepath, log_callback=None):
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in DUPLICATE_CHECK_EXTENSIONS:
            return (False, None, None)
        
        try:
            if os.path.getsize(filepath) == 0:
                return (False, None, None)
        except Exception:
            return (False, None, None)
        
        file_hash = self.get_file_hash(filepath)
        if not file_hash:
            return (False, None, None)
        
        duplicate_info = self._find_duplicate(file_hash, filepath)
        
        if duplicate_info:
            target_path = self._move_to_duplicate_folder(filepath, filename, duplicate_info)
            if target_path:
                if log_callback:
                    log_callback(f"🔄 检测到重复文件: {filename} (与 {duplicate_info['original']} 重复)")
                
                self.db_manager.duplicate_log.append({
                    'filename': filename,
                    'original': duplicate_info['original'],
                    'moved_to': target_path,
                    'file_hash': file_hash,
                    'timestamp': datetime.now().isoformat()
                })
                self.db_manager.save_duplicate_log()
                return (True, target_path, duplicate_info)
        
        return (False, None, None)
    
    def _find_duplicate(self, file_hash, current_path):
        for path, hash_value in self.db_manager.hash_cache.items():
            if path != current_path and hash_value == file_hash and os.path.exists(path):
                try:
                    rel_path = os.path.relpath(path, self.watch_folder)
                except Exception:
                    rel_path = path
                return {'original': rel_path, 'original_path': path, 'hash': file_hash}
        return None
    
    def _move_to_duplicate_folder(self, filepath, filename, duplicate_info):
        os.makedirs(self.duplicate_folder, exist_ok=True)
        target_path = os.path.join(self.duplicate_folder, filename)
        if os.path.exists(target_path):
            name, ext = os.path.splitext(filename)
            counter = 1
            while True:
                new_name = f"{name}_重复{counter}{ext}"
                new_path = os.path.join(self.duplicate_folder, new_name)
                if not os.path.exists(new_path):
                    target_path = new_path
                    break
                counter += 1
        
        try:
            shutil.move(filepath, target_path)
            return target_path
        except Exception as e:
            print(f"移动重复文件失败: {e}")
            return None
    
    def get_duplicate_stats(self):
        total_size = 0
        if os.path.exists(self.duplicate_folder):
            for root, dirs, files in os.walk(self.duplicate_folder):
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
        return {
            'duplicate_folder': self.duplicate_folder,
            'duplicate_count': len(self.db_manager.duplicate_log),
            'folder_size': total_size
        }


# ==================== 训练引擎 ====================
class TrainingEngine:
    def __init__(self, base_target_path, db_manager, log_callback=None):
        self.base_target_path = base_target_path
        self.db_manager = db_manager
        self.log_callback = log_callback
        self.training_data = []
        self.statistics = {
            'total_files': 0,
            'total_folders': 0,
            'trained_items': 0,
            'skipped_items': 0,
            'errors': 0,
            'duplicates_found': 0,
            'categories': defaultdict(int)
        }
    
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        if self.log_callback:
            self.log_callback(full_msg)
        print(full_msg)
    
    def _count_files(self, path):
        """预计算文件总数，用于进度条"""
        count = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.') or f.startswith('~$') or f.lower() in ['desktop.ini', 'thumbs.db', '.ds_store']:
                    continue
                count += 1
        return count
    
    def scan_target_folder(self, progress_callback=None):
        self.log("🔍 开始扫描目标文件夹...")
        self.log(f"📁 目标路径: {self.base_target_path}")
        
        if not os.path.exists(self.base_target_path):
            self.log("❌ 目标路径不存在")
            return []
        
        # 先统计总数，用于进度条
        total_files = self._count_files(self.base_target_path)
        self.log(f"📊 预计扫描 {total_files} 个文件...")
        
        learning_data = []
        file_count = 0
        folder_count = 0
        
        for root, dirs, files in os.walk(self.base_target_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            folder_count += 1
            
            relative_path = os.path.relpath(root, self.base_target_path)
            if relative_path == '.':
                relative_path = ''
            
            for file in files:
                if file.startswith('.') or file.startswith('~$'):
                    continue
                if file.lower() in ['desktop.ini', 'thumbs.db', '.ds_store']:
                    continue
                
                file_path = os.path.join(root, file)
                try:
                    file_size = os.path.getsize(file_path)
                    if file_size == 0:
                        continue
                except Exception:
                    continue
                
                file_name_without_ext = os.path.splitext(file)[0]
                if not file_name_without_ext:
                    continue
                
                learning_data.append({
                    'text': file_name_without_ext,
                    'path': relative_path if relative_path else '根目录',
                    'full_path': root,
                    'file_name': file,
                    'file_size': file_size,
                    'extension': os.path.splitext(file)[1].lower(),
                    'scanned_at': datetime.now().isoformat()
                })
                file_count += 1
                self.statistics['categories'][relative_path if relative_path else '根目录'] += 1
                
                if progress_callback and file_count % 100 == 0:
                    progress_callback(file_count, total_files, "扫描中...")
        
        self.statistics['total_files'] = file_count
        self.statistics['total_folders'] = folder_count
        
        self.log(f"✔ 扫描完成: 发现 {file_count} 个文件, {folder_count} 个文件夹")
        return learning_data
    
    def analyze_training_data(self, learning_data):
        self.log("📊 分析训练数据...")
        keyword_patterns = defaultdict(lambda: defaultdict(int))
        path_patterns = defaultdict(int)
        
        for item in learning_data:
            text = item['text']
            path = item['path']
            path_patterns[path] += 1
            
            words = re.findall(r'[\u4e00-\u9fff\w]+', text)
            for word in words:
                if len(word) >= 2:
                    keyword_patterns[word.lower()][path] += 1
            
            year_match = re.search(r'(20\d{2})', text)
            if year_match:
                keyword_patterns[year_match.group(1)][path] += 1
            
            abbrev_match = re.findall(r'[A-Z]{2,}', text)
            for abbr in abbrev_match:
                keyword_patterns[abbr.lower()][path] += 1
        
        self.log(f"✔ 分析完成: 发现 {len(keyword_patterns)} 个关键词模式, {len(path_patterns)} 个路径模式")
        return {'keyword_patterns': dict(keyword_patterns), 'path_patterns': dict(path_patterns)}
    
    def train_model(self, learning_data, progress_callback=None):
        self.log("🧠 开始训练学习模型...")
        if not learning_data:
            self.log("❌ 没有可用的训练数据")
            return False
        
        self.analyze_training_data(learning_data)
        
        total = len(learning_data)
        new_items = 0
        duplicate_items = 0
        
        # 构建 O(1) 查重集合
        existing_set = set()
        for item in self.db_manager.memory:
            existing_set.add((item['text'], item['path']))
        
        for i, item in enumerate(learning_data):
            text = item['text']
            path = item['path']
            key = (text, path)
            
            if key in existing_set:
                duplicate_items += 1
                self.statistics['duplicates_found'] += 1
            else:
                self.db_manager.memory.append({
                    'text': text,
                    'path': path,
                    'learned_at': datetime.now().isoformat(),
                    'source_file': item.get('file_name', ''),
                    'source_path': item.get('full_path', ''),
                    'extension': item.get('extension', '')
                })
                existing_set.add(key)
                new_items += 1
                self.statistics['trained_items'] += 1
            
            if progress_callback and i % 50 == 0:
                progress_callback(i, total, f"训练中: {i}/{total}")
        
        if len(self.db_manager.memory) > 10000:
            self.log("⚠️ 数据量超过限制，裁剪至10000条")
            self.db_manager.memory = self.db_manager.memory[-10000:]
        
        self.db_manager.save_learning_data()
        self.log(f"✔ 训练完成: 新增 {new_items} 条, 跳过重复 {duplicate_items} 条")
        return True
    
    def validate_training_data(self, learning_data):
        self.log("🔍 验证训练数据质量...")
        validation_result = {
            'valid': True,
            'issues': [],
            'warnings': [],
            'statistics': {
                'total': len(learning_data),
                'empty_text': 0,
                'empty_path': 0,
                'duplicate_entries': 0,
                'invalid_extension': 0,
                'small_files': 0,
                'recommended_entries': 0
            }
        }
        
        seen_entries = set()
        invalid_extensions = {'.tmp', '.log', '.cache', '.lock'}
        
        for item in learning_data:
            text = item.get('text', '').strip()
            path = item.get('path', '').strip()
            extension = item.get('extension', '')
            
            if not text:
                validation_result['statistics']['empty_text'] += 1
                validation_result['valid'] = False
                continue
            if not path:
                validation_result['statistics']['empty_path'] += 1
                validation_result['valid'] = False
                continue
            
            key = f"{text}|{path}"
            if key in seen_entries:
                validation_result['statistics']['duplicate_entries'] += 1
            seen_entries.add(key)
            
            if extension in invalid_extensions:
                validation_result['statistics']['invalid_extension'] += 1
                validation_result['warnings'].append(f"跳过了 {extension} 扩展名的文件")
            
            if len(text) < 2:
                validation_result['warnings'].append(f"文件名过短: {text}")
            
            if item.get('file_size', 0) < 1024:
                validation_result['statistics']['small_files'] += 1
        
        if validation_result['statistics']['total'] > 100:
            validation_result['statistics']['recommended_entries'] = min(
                validation_result['statistics']['total'], 1000
            )
        
        self.log("📊 验证报告:")
        self.log(f"  总条目: {validation_result['statistics']['total']}")
        self.log(f"  空文件名: {validation_result['statistics']['empty_text']}")
        self.log(f"  空路径: {validation_result['statistics']['empty_path']}")
        self.log(f"  重复条目: {validation_result['statistics']['duplicate_entries']}")
        
        if validation_result['warnings']:
            self.log("  警告:")
            for warning in set(validation_result['warnings']):
                self.log(f"    ⚠️ {warning}")
        
        return validation_result
    
    def generate_training_report(self):
        report = "=" * 60 + "\n"
        report += "📊 学习数据库训练报告\n"
        report += "=" * 60 + "\n\n"
        report += f"📁 目标路径: {self.base_target_path}\n"
        report += f"📅 训练时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        report += "📈 统计数据:\n"
        report += f"  总文件数: {self.statistics['total_files']}\n"
        report += f"  总文件夹数: {self.statistics['total_folders']}\n"
        report += f"  新增学习条目: {self.statistics['trained_items']}\n"
        report += f"  跳过重复: {self.statistics['duplicates_found']}\n"
        report += f"  错误数: {self.statistics['errors']}\n\n"
        report += "📂 分类统计 (Top 10):\n"
        sorted_categories = sorted(self.statistics['categories'].items(), key=lambda x: x[1], reverse=True)
        for category, count in sorted_categories[:10]:
            report += f"  {category}: {count}\n"
        if len(sorted_categories) > 10:
            report += f"  ... 还有 {len(sorted_categories) - 10} 个分类\n"
        report += f"\n📊 数据库状态:\n"
        report += f"  当前学习条目: {len(self.db_manager.memory)}\n"
        report += f"  数据库路径: {self.db_manager.base_path}\n"
        return report


# ==================== 核心分类引擎 ====================
class SmartOrganizerEngine:
    def __init__(self, watch_folder, base_target, db_manager, log_callback=None):
        self.watch_folder = watch_folder
        self.base_target = base_target
        self.db_manager = db_manager
        parent_folder = os.path.dirname(watch_folder)
        self.unknown_folder = os.path.join(parent_folder, "未分类文件")
        self.data_folder = os.path.join(parent_folder, "smart_organizer")
        self.log_file = os.path.join(self.data_folder, "file_log.txt")
        
        self.log_callback = log_callback
        self.model = None
        self.index = None
        self.dimension = 384
        self.is_running = False
        self.observer = None
        self.pending_files = queue.Queue()
        self.processed_files = set()
        self.processed_lock = threading.Lock()
        
        self.deduplicator = FileDeduplicator(watch_folder, db_manager)
        
        self.stats = {
            'total_processed': 0,
            'auto_classified': 0,
            'manual_classified': 0,
            'unknown_moved': 0,
            'duplicates_found': 0,
            'duplicates_moved': 0,
            'errors': 0
        }
        
        self._init_model()
        self._load_memory_to_index()
        os.makedirs(self.unknown_folder, exist_ok=True)
        os.makedirs(self.data_folder, exist_ok=True)
    
    def _init_model(self):
        self.log("🧠 正在加载本地语义模型...")
        try:
            self.model = SentenceTransformer(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )
            self.index = faiss.IndexFlatL2(self.dimension)
            self.log("✔ 模型加载完成")
        except Exception as e:
            self.log(f"❌ 模型加载失败: {e}")
            self.model = None
    
    def _load_memory_to_index(self):
        memory = self.db_manager.memory
        if not memory or self.model is None:
            return
        
        self.log(f"📦 加载 {len(memory)} 条学习数据到索引...")
        vectors = []
        for item in memory:
            vec = self.encode(item["text"])
            if vec is not None:
                vectors.append(vec)
            else:
                self.log(f"⚠ 跳过编码失败的条目: {item.get('text', '')[:30]}...（保留原始数据不删除）")
        
        if vectors:
            self.index.reset()
            self.index.add(np.array(vectors).astype("float32"))
            self.log(f"✔ 索引加载完成: {len(vectors)}/{len(memory)} 条")
        
        failed_count = len(memory) - len(vectors)
        if failed_count > 0:
            self.log(f"⚠ {failed_count} 条数据编码失败（已跳过，不删除原始数据）")
    
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        if self.log_callback:
            self.log_callback(full_msg)
        print(full_msg)
    
    def encode(self, text):
        if self.model is None:
            return None
        try:
            vec = self.model.encode([text], show_progress_bar=False)[0]
            return np.array(vec).astype("float32")
        except Exception as e:
            self.log(f"❌ 向量化失败: {e}")
            return None
    
    def predict_with_suggestions(self, text, top_k=3):
        if len(self.db_manager.memory) == 0 or self.model is None:
            return []
        
        vec = self.encode(text)
        if vec is None:
            return []
        
        vec = vec.reshape(1, -1)
        k = min(top_k, len(self.db_manager.memory))
        D, I = self.index.search(vec, k)
        
        suggestions = []
        seen_paths = set()
        for i in range(k):
            if I[0][i] != -1:
                path = self.db_manager.memory[I[0][i]]["path"]
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                similarity = max(0, 1 - D[0][i] / 2)
                if similarity >= MIN_CONFIDENCE_THRESHOLD:
                    suggestions.append((path, similarity))
        return suggestions[:top_k]
    
    def learn(self, text, path):
        if not text or not path:
            return
        
        # 先检查是否已存在
        for item in self.db_manager.memory:
            if item["text"] == text and item["path"] == path:
                self.log(f"ℹ️ 映射已存在: {text} -> {path}")
                return
        
        new_item = {
            "text": text, 
            "path": path, 
            "learned_at": datetime.now().isoformat()
        }
        
        if len(self.db_manager.memory) >= MAX_LEARNING_ITEMS:
            # 移除最旧条目，然后追加新条目
            self.db_manager.memory.pop(0)
        
        self.db_manager.memory.append(new_item)
        
        # 全量重建索引（一次性完成，避免双加向量）
        self.index.reset()
        vectors = []
        for item in self.db_manager.memory:
            v = self.encode(item["text"])
            if v is not None:
                vectors.append(v)
        if vectors:
            self.index.add(np.array(vectors).astype("float32"))
        
        self.db_manager.save_learning_data()
        self.log(f"📝 已学习: {text} -> {path}")
    
    def _should_skip_file(self, filename, filepath):
        temp_patterns = ["~$", ".tmp", ".temp", ".cache", ".lock", "._"]
        for pattern in temp_patterns:
            if pattern in filename:
                return True
        if filename.startswith(".") or filename.startswith("已处理_") or filename.startswith("processed_"):
            return True
        if filepath == self.log_file:
            return True
        if self.data_folder in filepath or self.unknown_folder in filepath:
            return True
        if self.deduplicator.duplicate_folder in filepath:
            return True
        
        try:
            stat = os.stat(filepath)
            file_key = f"{filename}_{stat.st_size}_{stat.st_mtime}"
            with self.processed_lock:
                if file_key in self.processed_files:
                    return True
        except Exception:
            pass
        return False
    
    def _mark_processed(self, filepath):
        try:
            stat = os.stat(filepath)
            filename = os.path.basename(filepath)
            file_key = f"{filename}_{stat.st_size}_{stat.st_mtime}"
            with self.processed_lock:
                self.processed_files.add(file_key)
                if len(self.processed_files) > 5000:
                    self.processed_files = set(list(self.processed_files)[-4000:])
        except Exception:
            pass
    
    def process_file(self, path, force=False):
        filename = os.path.basename(path)
        
        if not force and self._should_skip_file(filename, path):
            return 'skipped'
        
        self._mark_processed(path)
        self.log(f"📄 处理文件: {filename}")
        self.stats['total_processed'] += 1
        
        if not os.path.exists(path):
            self.log(f"⚠ 文件不存在: {path}")
            return 'error'
        
        try:
            file_size = os.path.getsize(path)
            if file_size == 0:
                self.log(f"⚠ 空文件，跳过: {filename}")
                return 'skipped'
            if file_size > 2 * 1024 * 1024 * 1024:
                self.log(f"⚠ 文件过大，跳过: {filename}")
                return 'skipped'
        except Exception:
            pass
        
        is_duplicate, target_path, dup_info = self.deduplicator.check_and_handle_duplicate(
            path, log_callback=self.log
        )
        
        if is_duplicate:
            self.stats['duplicates_found'] += 1
            self.stats['duplicates_moved'] += 1
            return 'duplicate'
        
        suggestions = self.predict_with_suggestions(filename, top_k=3)
        
        if suggestions and suggestions[0][1] >= MIN_CONFIDENCE_THRESHOLD:
            target_sub = suggestions[0][0]
            self.log(f"🤖 自动分类: {filename} -> {target_sub}")
            success = self._move_file(path, filename, target_sub)
            if success:
                self.stats['auto_classified'] += 1
                return 'auto'
            return 'error'
        else:
            self.log(f"⏭️ 无匹配建议，移至未分类: {filename}")
            self._move_to_unknown(path, filename)
            self.stats['unknown_moved'] += 1
            return 'unknown'
    
    def _move_to_unknown(self, path, filename):
        os.makedirs(self.unknown_folder, exist_ok=True)
        unknown_path = os.path.join(self.unknown_folder, filename)
        if os.path.exists(unknown_path):
            name, ext = os.path.splitext(filename)
            counter = 1
            while True:
                new_name = f"{name}_{counter}{ext}"
                new_path = os.path.join(self.unknown_folder, new_name)
                if not os.path.exists(new_path):
                    unknown_path = new_path
                    break
                counter += 1
        
        try:
            shutil.move(path, unknown_path)
            self.log(f"⏭️ 已移至未分类: {os.path.basename(unknown_path)}")
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
            self.stats['errors'] += 1
    
    def _move_file(self, path, filename, target_sub):
        target_dir = os.path.join(self.base_target, target_sub)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)
        
        if os.path.exists(target_path):
            name, ext = os.path.splitext(filename)
            counter = 1
            while True:
                new_name = f"{name}_{counter}{ext}"
                new_path = os.path.join(target_dir, new_name)
                if not os.path.exists(new_path):
                    target_path = new_path
                    break
                counter += 1
        
        try:
            shutil.move(path, target_path)
            self.log(f"✅ 已移动: {filename} -> {target_sub}")
            self.learn(filename, target_sub)
            return True
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
            self.stats['errors'] += 1
            return False
    
    def process_unknown_file(self, filepath, target_sub):
        filename = os.path.basename(filepath)
        return self._move_file(filepath, filename, target_sub)
    
    def handle_user_choice(self, filename, path, choice, suggestions):
        if choice is None:
            self._move_to_unknown(path, filename)
            self.stats['unknown_moved'] += 1
            return
        
        if isinstance(choice, int) and choice < len(suggestions):
            target_sub = suggestions[choice][0]
        else:
            target_sub = choice
        
        if self._move_file(path, filename, target_sub):
            self.stats['manual_classified'] += 1
    
    def start(self):
        if self.is_running:
            return
        
        self.is_running = True
        os.makedirs(self.unknown_folder, exist_ok=True)
        os.makedirs(self.data_folder, exist_ok=True)
        
        self.log("🚀 启动文件监听器...")
        self.log(f"📁 数据库路径: {self.db_manager.base_path}")
        self.log(f"📁 未分类文件夹: {self.unknown_folder}")
        self.log(f"📁 重复文件夹: {self.deduplicator.duplicate_folder}")
        self.log(f"📊 学习数据: {len(self.db_manager.memory)} 条")
        
        self.observer = Observer()
        handler = self._create_handler()
        self.observer.schedule(handler, self.watch_folder, recursive=False)
        self.observer.start()
        self.log("✔ 文件监听已启动")
    
    def _create_handler(self):
        engine = self
        
        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                time.sleep(0.5)
                if os.path.exists(event.src_path):
                    engine.pending_files.put(("process", event.src_path))
            
            def on_moved(self, event):
                if not event.is_directory:
                    time.sleep(0.5)
                    if os.path.exists(event.dest_path):
                        engine.pending_files.put(("process", event.dest_path))
        
        return Handler()
    
    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.is_running = False
        self.log("🛑 监听器已停止")
    
    def get_pending_file(self):
        try:
            return self.pending_files.get_nowait()
        except queue.Empty:
            return None
    
    def get_stats(self):
        dup_stats = self.deduplicator.get_duplicate_stats()
        db_stats = self.db_manager.get_stats()
        return {
            **self.stats,
            'learning_items': len(self.db_manager.memory),
            'unknown_folder_size': self._get_folder_size(self.unknown_folder),
            'watch_folder_files': len([f for f in os.listdir(self.watch_folder) 
                                      if os.path.isfile(os.path.join(self.watch_folder, f))]),
            'duplicate_folder': dup_stats['duplicate_folder'],
            'duplicate_count': dup_stats['duplicate_count'],
            'duplicate_folder_size': dup_stats['folder_size'],
            'db_stats': db_stats
        }
    
    def _get_folder_size(self, folder):
        total = 0
        if os.path.exists(folder):
            for root, dirs, files in os.walk(folder):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
        return total
    
    def process_existing_files(self, progress_callback=None):
        self.log("📂 检查现有文件...")
        files = []
        for f in os.listdir(self.watch_folder):
            file_path = os.path.join(self.watch_folder, f)
            if os.path.isfile(file_path):
                files.append(file_path)
        
        if not files:
            self.log("✔ 没有需要处理的现有文件")
            return {'processed': 0, 'auto': 0, 'unknown': 0, 'duplicate': 0}
        
        self.log(f"发现 {len(files)} 个现有文件待处理")
        results = {'processed': 0, 'auto': 0, 'unknown': 0, 'duplicate': 0, 'skipped': 0, 'error': 0}
        
        for i, file_path in enumerate(files):
            if progress_callback:
                progress_callback(i, len(files), os.path.basename(file_path))
            result = self.process_file(file_path)
            if result in results:
                results[result] += 1
        
        self.log(f"✔ 现有文件处理完成: {results}")
        return results


# ==================== 目录树选择对话框 ====================
class EnhancedFolderTreeDialog:
    def __init__(self, parent, base_path, filename, current_path="",
                 suggestions=None, engine=None, show_preview=True):
        self.parent = parent
        self.base_path = base_path
        self.filename = filename
        self.current_path = current_path
        self.suggestions = suggestions or []
        self.engine = engine
        self.show_preview = show_preview
        self.selected_path = None
        self.result = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"选择分类目录 - {filename}")
        self.dialog.geometry("750x600")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self._create_widgets()
        self._load_tree()
        
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 750) // 2
        y = (self.dialog.winfo_screenheight() - 600) // 2
        self.dialog.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        main_paned = ttk.PanedWindow(self.dialog, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=2)
        
        info_frame = ttk.Frame(left_frame)
        info_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(info_frame, text=f"📄 {self.filename}", font=("", 10, "bold")).pack(anchor=tk.W)
        
        if self.suggestions:
            suggest_frame = ttk.LabelFrame(left_frame, text="💡 匹配建议", padding="5")
            suggest_frame.pack(fill=tk.X, pady=(0, 5))
            for idx, (path, score) in enumerate(self.suggestions):
                btn = ttk.Button(suggest_frame, text=f"📁 {path} ({score:.2f})",
                               command=lambda i=idx: self._quick_select(i))
                btn.pack(side=tk.LEFT, padx=2, pady=2)
        
        path_frame = ttk.Frame(left_frame)
        path_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(path_frame, text="路径:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.current_path or self.base_path)
        ttk.Entry(path_frame, textvariable=self.path_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(tree_frame, yscrollcommand=scrollbar.set, height=12)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.tree.yview)
        self.tree.bind("<Double-1>", self._on_double_click)
        
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="📁 上级目录", command=self._go_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📂 选择此目录", command=self._confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📂 选择并学习", command=self._confirm_and_learn).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="⏭️ 跳过", command=self._skip).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="取消", command=self._cancel).pack(side=tk.RIGHT, padx=2)
        
        if self.show_preview:
            right_frame = ttk.Frame(main_paned)
            main_paned.add(right_frame, weight=1)
            preview_frame = ttk.LabelFrame(right_frame, text="📋 文件预览", padding="10")
            preview_frame.pack(fill=tk.BOTH, expand=True)
            self.preview_text = scrolledtext.ScrolledText(preview_frame, height=15, font=("Consolas", 9))
            self.preview_text.pack(fill=tk.BOTH, expand=True)
            self._show_file_info()
    
    def _show_file_info(self):
        if not self.filename:
            return
        info = f"文件名: {self.filename}\n"
        info += f"扩展名: {os.path.splitext(self.filename)[1] or '无'}\n"
        info += "-" * 40 + "\n"
        self.preview_text.insert(tk.END, info)
        self.preview_text.config(state=tk.DISABLED)
    
    def _quick_select(self, idx):
        if idx < len(self.suggestions):
            self.selected_path = self.suggestions[idx][0]
            self.result = "confirm"
            self.dialog.destroy()
    
    def _load_tree(self, path=None):
        if path is None:
            path = self.current_path or self.base_path
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        try:
            items = []
            for name in os.listdir(path):
                full_path = os.path.join(path, name)
                if os.path.isdir(full_path) and not name.startswith("."):
                    has_children = any(
                        os.path.isdir(os.path.join(full_path, sub)) and not sub.startswith(".")
                        for sub in os.listdir(full_path)
                    ) if os.path.exists(full_path) else False
                    items.append((name, full_path, has_children))
            
            items.sort(key=lambda x: x[0])
            for name, full_path, has_children in items:
                node = self.tree.insert("", "end", text=name, values=(full_path, has_children))
                if has_children:
                    self.tree.insert(node, "end", text="")
            
            self.path_var.set(path)
        except Exception as e:
            print(f"加载目录失败: {e}")
    
    def _on_double_click(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        values = self.tree.item(item, "values")
        if len(values) < 1:
            return
        full_path = values[0]
        if os.path.isdir(full_path):
            self.current_path = full_path
            self._load_tree(full_path)
    
    def _go_up(self):
        parent = os.path.dirname(self.current_path)
        if parent and os.path.exists(parent) and parent.startswith(self.base_path):
            self.current_path = parent
            self._load_tree(parent)
        else:
            messagebox.showinfo("提示", "已在根目录")
    
    def _confirm(self):
        selected = self.tree.selection()
        if selected:
            full_path = self.tree.item(selected[0], "values")[0]
            relative_path = full_path.replace(self.base_path, "").strip("\\/")
            self.selected_path = relative_path
            self.result = "confirm"
            self.dialog.destroy()
        else:
            messagebox.showwarning("提示", "请选择一个目录")
    
    def _confirm_and_learn(self):
        selected = self.tree.selection()
        if selected:
            full_path = self.tree.item(selected[0], "values")[0]
            relative_path = full_path.replace(self.base_path, "").strip("\\/")
            self.selected_path = relative_path
            self.result = "learn"
            self.dialog.destroy()
        else:
            messagebox.showwarning("提示", "请选择一个目录")
    
    def _skip(self):
        self.result = "skip"
        self.dialog.destroy()
    
    def _cancel(self):
        self.result = "cancel"
        self.dialog.destroy()


# ==================== 训练系统GUI ====================
class TrainingSystemGUI:
    def __init__(self, parent, db_manager, on_close_callback=None):
        self.parent = parent
        self.db_manager = db_manager
        self.on_close_callback = on_close_callback
        self.training_engine = None
        self.scanned_data = None
        
        self._create_window()
    
    def _create_window(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("学习数据库训练系统")
        self.window.geometry("900x700")
        self.window.transient(self.parent)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 顶部：返回按钮
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(top_frame, text="🏠 返回主程序", 
                  command=self._on_close).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(top_frame, text="学习数据库训练系统", 
                 font=("", 12, "bold")).pack(side=tk.LEFT, padx=20)
        
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        
        # 配置区域
        config_frame = ttk.LabelFrame(main_frame, text="训练配置", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(config_frame, text="📁 目标文件夹:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.target_path_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.target_path_var, width=60).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(config_frame, text="浏览", command=self._browse_target).grid(row=0, column=2, pady=5)
        
        options_frame = ttk.Frame(config_frame)
        options_frame.grid(row=1, column=0, columnspan=3, pady=10, sticky=tk.W)
        
        self.validate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="验证数据质量", variable=self.validate_var).pack(side=tk.LEFT, padx=5)
        
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="增量学习（跳过重复）", variable=self.incremental_var).pack(side=tk.LEFT, padx=5)
        
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=5)
        
        self.scan_btn = ttk.Button(control_frame, text="🔍 扫描目标文件夹", command=self._scan_and_analyze)
        self.scan_btn.pack(side=tk.LEFT, padx=2)
        
        self.train_btn = ttk.Button(control_frame, text="🧠 开始训练", command=self._start_training, state=tk.DISABLED)
        self.train_btn.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(control_frame, text="📊 生成报告", command=self._show_report).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📤 导出数据库", command=self._export_database).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📥 导入数据库", command=self._import_database).pack(side=tk.LEFT, padx=2)
        
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100).pack(fill=tk.X)
        self.progress_label = ttk.Label(progress_frame, text="就绪")
        self.progress_label.pack(anchor=tk.W, pady=2)
        
        info_frame = ttk.LabelFrame(main_frame, text="训练信息", padding="10")
        info_frame.pack(fill=tk.X, pady=5)
        self.info_text = tk.Text(info_frame, height=6, font=("Consolas", 9))
        self.info_text.pack(fill=tk.X)
        self._update_info()
        
        log_frame = ttk.LabelFrame(main_frame, text="📝 训练日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)
    
    def _on_close(self):
        """关闭窗口时回调"""
        if self.on_close_callback:
            self.on_close_callback()
        self.window.destroy()
    
    def _browse_target(self):
        path = filedialog.askdirectory(title="选择目标文件夹（包含已分类文件）")
        if path:
            self.target_path_var.set(path)
    
    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.window.update_idletasks()
    
    def _update_info(self):
        self.info_text.delete(1.0, tk.END)
        info = f"📊 数据库状态:\n"
        info += f"  路径: {self.db_manager.base_path}\n"
        info += f"  学习条目: {len(self.db_manager.memory)}\n"
        info += f"  重复记录: {len(self.db_manager.duplicate_log)}\n"
        info += f"  哈希缓存: {len(self.db_manager.hash_cache)}\n"
        info += f"  数据库版本: {self.db_manager.DB_VERSION}\n"
        self.info_text.insert(tk.END, info)
        self.info_text.config(state=tk.DISABLED)
    
    def _scan_and_analyze(self):
        target_path = self.target_path_var.get().strip()
        if not target_path:
            messagebox.showwarning("提示", "请选择目标文件夹")
            return
        if not os.path.exists(target_path):
            messagebox.showerror("错误", "目标文件夹不存在")
            return
        
        self.training_engine = TrainingEngine(target_path, self.db_manager, log_callback=self._log)
        
        self._log("=" * 50)
        self._log("🚀 开始扫描分析...")
        
        self.scan_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="扫描中...")
        self.status_var.set("扫描中...")
        
        def scan_thread():
            try:
                learning_data = self.training_engine.scan_target_folder(
                    progress_callback=self._update_scan_progress
                )
                self.scanned_data = learning_data
                
                if learning_data:
                    self._log(f"✔ 扫描完成: 发现 {len(learning_data)} 个有效文件")
                    if self.validate_var.get():
                        validation = self.training_engine.validate_training_data(learning_data)
                        self._log(f"📊 验证完成: {validation['statistics']['total']} 条数据")
                    self.train_btn.config(state=tk.NORMAL)
                    self._log("💡 点击「开始训练」将数据添加到学习数据库")
                    self.status_var.set(f"扫描完成: 发现 {len(learning_data)} 个文件")
                else:
                    self._log("⚠️ 未发现有效的训练数据")
                    self.status_var.set("扫描完成: 无有效数据")
            except Exception as e:
                self._log(f"❌ 扫描失败: {e}")
                self.status_var.set("扫描失败")
            finally:
                self.scan_btn.config(state=tk.NORMAL)
                self.progress_var.set(0)
                self.progress_label.config(text="就绪")
        
        threading.Thread(target=scan_thread, daemon=True).start()
    
    def _update_scan_progress(self, current, total, status):
        self.window.after(0, lambda: self.progress_label.config(text=f"{status}: {current} 个文件"))
        progress = min((current / max(total, 1)) * 100, 100)
        self.window.after(0, lambda: self.progress_var.set(progress))
    
    def _start_training(self):
        if not self.training_engine or not self.scanned_data:
            messagebox.showwarning("提示", "请先扫描目标文件夹")
            return
        
        if not self.scanned_data:
            messagebox.showwarning("提示", "没有可用的训练数据")
            return
        
        result = messagebox.askyesno("确认训练",
            f"将添加 {len(self.scanned_data)} 条学习数据到数据库\n"
            f"当前数据库有 {len(self.db_manager.memory)} 条记录\n是否继续？")
        
        if not result:
            return
        
        self._log("=" * 50)
        self._log("🧠 开始训练...")
        
        self.train_btn.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.status_var.set("训练中...")
        
        def train_thread():
            try:
                success = self.training_engine.train_model(
                    self.scanned_data,
                    progress_callback=self._update_train_progress
                )
                if success:
                    self._log("✔ 训练完成")
                    self._update_info()
                    self.status_var.set(f"训练完成: 新增 {self.training_engine.statistics['trained_items']} 条")
                    messagebox.showinfo("成功", f"训练完成！\n新增 {self.training_engine.statistics['trained_items']} 条学习数据")
                else:
                    self._log("❌ 训练失败")
                    self.status_var.set("训练失败")
            except Exception as e:
                self._log(f"❌ 训练错误: {e}")
                self.status_var.set("训练错误")
            finally:
                self.train_btn.config(state=tk.NORMAL)
                self.scan_btn.config(state=tk.NORMAL)
                self.progress_var.set(0)
                self.progress_label.config(text="就绪")
        
        threading.Thread(target=train_thread, daemon=True).start()
    
    def _update_train_progress(self, current, total, status):
        progress = (current / total) * 100 if total > 0 else 0
        self.window.after(0, lambda: self.progress_var.set(progress))
        self.window.after(0, lambda: self.progress_label.config(text=f"{status}: {current}/{total}"))
    
    def _show_report(self):
        if not self.training_engine:
            messagebox.showwarning("提示", "请先执行扫描或训练")
            return
        
        report = self.training_engine.generate_training_report()
        report_window = tk.Toplevel(self.window)
        report_window.title("训练报告")
        report_window.geometry("600x500")
        
        text_widget = scrolledtext.ScrolledText(report_window, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(1.0, report)
        text_widget.config(state=tk.DISABLED)
        
        btn_frame = ttk.Frame(report_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        def save_report():
            file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(report)
                messagebox.showinfo("成功", f"报告已保存到:\n{file_path}")
        
        ttk.Button(btn_frame, text="💾 保存报告", command=save_report).pack(side=tk.LEFT, padx=2)
    
    def _export_database(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if file_path:
            result = self.db_manager.export_learning_data(file_path)
            if result:
                messagebox.showinfo("成功", f"数据库已导出到:\n{result}")
                self._log(f"📤 数据库已导出: {result}")
            else:
                messagebox.showerror("错误", "导出失败")
    
    def _import_database(self):
        file_path = filedialog.askopenfilename(title="选择数据文件", filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if file_path:
            success, msg = self.db_manager.import_learning_data(file_path)
            if success:
                messagebox.showinfo("成功", msg)
                self._update_info()
                self._log(f"📥 数据库导入成功: {msg}")
            else:
                messagebox.showerror("错误", msg)
                self._log(f"❌ 数据库导入失败: {msg}")


# ==================== 主应用程序 ====================
class SmartOrganizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("智能文件分类器 v6 - 完整整合版")
        self.root.geometry("1100x800")
        self.root.resizable(True, True)
        
        self.db_manager = DatabaseManager()
        self.engine = None
        self.is_auto_mode = tk.BooleanVar(value=False)
        self.is_running = False
        self.current_file = None
        self.training_window = None
        
        self._create_widgets()
        
        self.watch_entry.insert(0, DEFAULT_WATCH_FOLDER)
        self.target_entry.insert(0, DEFAULT_BASE_TARGET)
        
        self.log("=" * 60)
        self.log("👋 欢迎使用智能文件分类器 v6 - 完整整合版")
        self.log(f"📁 数据库路径: {self.db_manager.base_path}")
        self.log(f"📊 学习数据: {len(self.db_manager.memory)} 条")
        self.log(f"🔄 重复文件: {len(self.db_manager.duplicate_log)} 条")
        self.log("📌 数据库独立存储，程序更新不影响学习数据")
        self.log("📌 点击「🧠 训练系统」可扫描目标文件夹训练模型")
        self.log("=" * 60)
        self.log("请检查配置路径，然后点击「🚀 启动服务」")
    
    def _create_widgets(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        top_frame = ttk.Frame(main_paned)
        main_paned.add(top_frame, weight=1)
        
        # 配置区域
        config_frame = ttk.LabelFrame(top_frame, text="⚙️ 配置", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(config_frame, text="📁 监听目录:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.watch_entry = ttk.Entry(config_frame, width=50)
        self.watch_entry.grid(row=0, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_watch).grid(row=0, column=2, pady=2)
        
        ttk.Label(config_frame, text="🎯 目标目录:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.target_entry = ttk.Entry(config_frame, width=50)
        self.target_entry.grid(row=1, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_target).grid(row=1, column=2, pady=2)
        
        # 控制按钮区域
        control_frame = ttk.Frame(config_frame)
        control_frame.grid(row=2, column=0, columnspan=3, pady=(10, 0))
        
        ttk.Checkbutton(control_frame, text="🤖 自动模式", variable=self.is_auto_mode).pack(side=tk.LEFT, padx=(0, 15))
        
        self.start_btn = ttk.Button(control_frame, text="🚀 启动服务", command=self._toggle_service)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(control_frame, text="📂 处理现有", command=self._process_existing).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📂 批量处理未分类", command=self._batch_process_unknown).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="🔄 查看重复文件", command=self._view_duplicates).pack(side=tk.LEFT, padx=2)
        
        ttk.Separator(control_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y, ipady=10)
        
        ttk.Button(control_frame, text="🧠 训练系统", command=self._open_training_system).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📊 统计", command=self._show_stats).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📚 学习数据", command=self._show_memory).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📤 导出数据", command=self._export_data).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="💾 备份数据", command=self._backup_data).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="🗑️ 清空数据", command=self._clear_data).pack(side=tk.LEFT, padx=2)
        
        # 状态信息栏
        status_info_frame = ttk.Frame(config_frame)
        status_info_frame.grid(row=3, column=0, columnspan=3, pady=(10, 0), sticky=tk.W)
        
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_info_frame, textvariable=self.status_var, font=("", 9, "bold")).pack(side=tk.LEFT)
        ttk.Separator(status_info_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y, ipady=5)
        
        self.stats_var = tk.StringVar(value="处理: 0 | 自动: 0 | 手动: 0 | 未分类: 0 | 重复: 0")
        ttk.Label(status_info_frame, textvariable=self.stats_var, font=("", 9)).pack(side=tk.LEFT)
        ttk.Separator(status_info_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y, ipady=5)
        
        self.db_info_var = tk.StringVar(value=f"📊 学习: {len(self.db_manager.memory)} | 重复: {len(self.db_manager.duplicate_log)}")
        ttk.Label(status_info_frame, textvariable=self.db_info_var, font=("", 9), foreground="blue").pack(side=tk.LEFT)
        
        # 交互区域
        interact_frame = ttk.LabelFrame(top_frame, text="📋 文件分类确认", padding="10")
        interact_frame.pack(fill=tk.X, pady=5)
        
        self.interact_label = ttk.Label(interact_frame, text="等待文件...", font=("", 10, "bold"))
        self.interact_label.pack(anchor=tk.W)
        
        self.suggest_frame = ttk.Frame(interact_frame)
        self.suggest_frame.pack(fill=tk.X, pady=(5, 0))
        self.suggest_buttons = []
        
        manual_frame = ttk.Frame(interact_frame)
        manual_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(manual_frame, text="自定义路径:").pack(side=tk.LEFT)
        self.manual_entry = ttk.Entry(manual_frame, width=40)
        self.manual_entry.pack(side=tk.LEFT, padx=5)
        self.manual_entry.bind("<Return>", lambda e: self._manual_confirm())
        
        ttk.Button(manual_frame, text="📂 浏览目录", command=self._open_folder_tree).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="✅ 确认", command=self._manual_confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="⏭️ 跳过", command=self._skip_file).pack(side=tk.LEFT, padx=2)
        
        self.hint_label = ttk.Label(interact_frame, text="💡 选择建议路径或点击「浏览目录」从树形目录选择", foreground="gray")
        self.hint_label.pack(anchor=tk.W, pady=(5, 0))
        self._set_interact_enabled(False)
        
        # 日志区域
        log_frame = ttk.LabelFrame(top_frame, text="📝 日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        log_control = ttk.Frame(log_frame)
        log_control.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(log_control, text="清空日志", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(log_control, text="导出日志", command=self._export_log).pack(side=tk.LEFT, padx=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(log_control, variable=self.progress_var, maximum=100, length=200)
        self.progress_bar.pack(side=tk.RIGHT)
        self.progress_label = ttk.Label(log_control, text="")
        self.progress_label.pack(side=tk.RIGHT, padx=5)
    
    def _set_interact_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in self.suggest_buttons:
            btn.config(state=state)
        self.manual_entry.config(state=state)
    
    def _browse_watch(self):
        path = filedialog.askdirectory(title="选择监听目录")
        if path:
            self.watch_entry.delete(0, tk.END)
            self.watch_entry.insert(0, path)
    
    def _browse_target(self):
        path = filedialog.askdirectory(title="选择目标目录")
        if path:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, path)
    
    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)
    
    def _export_log(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.log_text.get(1.0, tk.END))
            self.log(f"📤 日志已导出: {file_path}")
    
    def _toggle_service(self):
        if not self.is_running:
            self._start_service()
        else:
            self._stop_service()
    
    def _start_service(self):
        watch_folder = self.watch_entry.get().strip()
        base_target = self.target_entry.get().strip()
        
        if not os.path.exists(watch_folder):
            messagebox.showerror("错误", f"监听目录不存在: {watch_folder}")
            return
        if not os.path.exists(base_target):
            messagebox.showerror("错误", f"目标目录不存在: {base_target}")
            return
        
        try:
            self.engine = SmartOrganizerEngine(watch_folder, base_target, self.db_manager, log_callback=self.log)
            self.engine.start()
            self.is_running = True
            self.start_btn.config(text="🛑 停止服务")
            self.status_var.set("🟢 运行中")
            self.log(f"✔ 服务已启动，监听: {watch_folder}")
            self._start_background_thread()
            self._update_stats()
        except Exception as e:
            self.log(f"❌ 启动失败: {e}")
            messagebox.showerror("错误", f"启动失败: {e}")
    
    def _stop_service(self):
        if self.engine:
            self.engine.stop()
        self.is_running = False
        self.start_btn.config(text="🚀 启动服务")
        self.status_var.set("🔴 已停止")
        self.log("🛑 服务已停止")
        self._set_interact_enabled(False)
        self._clear_interaction()
    
    def _start_background_thread(self):
        def background_worker():
            while self.is_running:
                if self.engine:
                    pending = self.engine.get_pending_file()
                    if pending:
                        cmd, data = pending
                        if cmd == "process":
                            self.root.after(0, lambda: self._handle_auto_process(data))
                time.sleep(0.5)
        threading.Thread(target=background_worker, daemon=True).start()
    
    def _handle_auto_process(self, path):
        if self.engine:
            result = self.engine.process_file(path)
            self._update_stats()
            if result == 'unknown':
                self.root.after(0, lambda: self._prompt_manual_classify(path))
    
    def _prompt_manual_classify(self, path):
        if not os.path.exists(path):
            return
        filename = os.path.basename(path)
        suggestions = self.engine.predict_with_suggestions(filename, top_k=3)
        self.current_file = {'filename': filename, 'path': path, 'suggestions': suggestions}
        self._show_interaction(self.current_file)
    
    def _show_interaction(self, data):
        filename = data["filename"]
        suggestions = data["suggestions"]
        self.current_file = data
        
        if suggestions:
            self.interact_label.config(text=f"📄 新文件: {filename}  —  请选择分类方式", foreground="blue")
            self.hint_label.config(text="💡 点击建议按钮，或点击「浏览目录」从树形目录选择")
        else:
            self.interact_label.config(text=f"📄 新文件: {filename}  —  暂无匹配建议，请手动选择", foreground="orange")
            self.hint_label.config(text="💡 暂无相似历史记录，点击「浏览目录」选择分类路径")
        
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        
        if suggestions:
            for idx, (path, score) in enumerate(suggestions):
                btn = ttk.Button(self.suggest_frame, text=f"📁 {path} ({score:.2f})",
                               command=lambda i=idx: self._suggest_confirm(i))
                btn.pack(side=tk.LEFT, padx=2, pady=2)
                self.suggest_buttons.append(btn)
        
        self.manual_entry.delete(0, tk.END)
        self._set_interact_enabled(True)
        self.manual_entry.focus_set()
        self.status_var.set(f"⏳ 等待分类: {filename}")
        self.root.attributes('-topmost', True)
        self.root.attributes('-topmost', False)
    
    def _open_folder_tree(self):
        if not self.engine:
            return
        base_path = self.engine.base_target
        filename = self.current_file["filename"] if self.current_file else ""
        current_path = self.manual_entry.get().strip()
        if current_path and os.path.exists(os.path.join(base_path, current_path)):
            full_current = os.path.join(base_path, current_path)
        else:
            full_current = base_path
        
        dialog = EnhancedFolderTreeDialog(
            self.root, base_path, filename, full_current,
            suggestions=self.current_file.get("suggestions", []) if self.current_file else [],
            engine=self.engine, show_preview=True
        )
        self.root.wait_window(dialog.dialog)
        
        if dialog.result == "confirm" or dialog.result == "learn":
            selected_path = dialog.selected_path
            if selected_path:
                self.manual_entry.delete(0, tk.END)
                self.manual_entry.insert(0, selected_path)
                if dialog.result == "learn":
                    self.log(f"📝 选择并学习: {filename} -> {selected_path}")
                    self._manual_confirm()
                else:
                    self.log(f"📝 选择: {filename} -> {selected_path}")
        elif dialog.result == "skip":
            self._skip_file()
    
    def _suggest_confirm(self, idx):
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            suggestions = self.current_file.get("suggestions", [])
            if idx < len(suggestions):
                target = suggestions[idx][0]
                self.log(f"📝 选择建议: {filename} -> {target}")
                self.engine.handle_user_choice(
                    self.current_file["filename"],
                    self.current_file["path"],
                    idx, suggestions
                )
                self._update_stats()
                self._clear_interaction()
    
    def _manual_confirm(self):
        target = self.manual_entry.get().strip()
        if not target:
            messagebox.showwarning("提示", "请输入分类路径\n或点击「浏览目录」选择")
            return
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            self.log(f"📝 手动分类: {filename} -> {target}")
            self.engine.handle_user_choice(
                self.current_file["filename"],
                self.current_file["path"],
                target,
                self.current_file.get("suggestions", [])
            )
            self._update_stats()
            self.manual_entry.delete(0, tk.END)
            self._clear_interaction()
    
    def _skip_file(self):
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            self.log(f"⏭️ 跳过: {filename} -> 移至未分类")
            self.engine.handle_user_choice(
                self.current_file["filename"],
                self.current_file["path"],
                None,
                self.current_file.get("suggestions", [])
            )
            self._update_stats()
            self._clear_interaction()
    
    def _clear_interaction(self):
        self.current_file = None
        self.interact_label.config(text="等待文件...", foreground="black")
        self.hint_label.config(text="💡 选择建议路径或点击「浏览目录」从树形目录选择")
        self._set_interact_enabled(False)
        self.status_var.set("🟢 运行中")
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        self.manual_entry.delete(0, tk.END)
    
    def _open_training_system(self):
        """打开训练系统窗口"""
        if self.training_window and self.training_window.winfo_exists():
            self.training_window.lift()
            return
        
        def on_training_close():
            self.training_window = None
        
        self.training_window = TrainingSystemGUI(
            self.root, 
            self.db_manager,
            on_close_callback=on_training_close
        )
    
    def _process_existing(self):
        if not self.engine or not self.is_running:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        self.log("📂 开始处理现有文件...")
        self.progress_bar["maximum"] = 100
        self.progress_var.set(0)
        
        def progress_callback(current, total, filename):
            progress = (current / total) * 100
            self.progress_var.set(progress)
            self.progress_label.config(text=f"{current}/{total}: {filename}")
            self.root.update_idletasks()
        
        results = self.engine.process_existing_files(progress_callback)
        self._update_stats()
        self.progress_var.set(0)
        self.progress_label.config(text="")
        self.log(f"✔ 处理完成: 自动 {results['auto']}, 未分类 {results['unknown']}, 重复 {results['duplicate']}")
    
    def _batch_process_unknown(self):
        if not self.engine or not self.is_running:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        unknown_folder = self.engine.unknown_folder
        if not os.path.exists(unknown_folder):
            messagebox.showinfo("提示", "未分类文件夹为空")
            return
        
        files = [f for f in os.listdir(unknown_folder) if os.path.isfile(os.path.join(unknown_folder, f))]
        if not files:
            messagebox.showinfo("提示", "未分类文件夹为空")
            return
        
        self.log(f"📂 开始批量处理未分类文件，共 {len(files)} 个")
        self.status_var.set(f"批量处理中... 共 {len(files)} 个")
        
        processed = 0
        for i, filename in enumerate(files, 1):
            filepath = os.path.join(unknown_folder, filename)
            self.log(f"[{i}/{len(files)}] 处理: {filename}")
            
            suggestions = self.engine.predict_with_suggestions(filename, top_k=3)
            
            if suggestions and self.is_auto_mode.get() and suggestions[0][1] >= MIN_CONFIDENCE_THRESHOLD:
                target_sub = suggestions[0][0]
                self.log(f"🤖 自动处理: {filename} -> {target_sub}")
                self.engine.process_unknown_file(filepath, target_sub)
                processed += 1
                continue
            
            dialog = EnhancedFolderTreeDialog(
                self.root, self.engine.base_target, filename,
                self.engine.base_target, suggestions=suggestions,
                engine=self.engine, show_preview=True
            )
            self.root.wait_window(dialog.dialog)
            
            if dialog.result == "confirm" or dialog.result == "learn":
                target_sub = dialog.selected_path
                success = self.engine.process_unknown_file(filepath, target_sub)
                if success and dialog.result == "learn":
                    self.engine.learn(filename, target_sub)
                processed += 1
                self.log(f"✅ 已完成 ({i}/{len(files)}): {filename} -> {target_sub}")
            elif dialog.result == "skip":
                self.log(f"⏭️ 跳过: {filename}")
                continue
            else:
                remaining = len(files) - i
                if remaining > 0:
                    self.log(f"🛑 取消，剩余 {remaining} 个文件")
                break
            
            self.progress_var.set((i / len(files)) * 100)
            self.progress_label.config(text=f"{i}/{len(files)}")
        
        self._update_stats()
        self.progress_var.set(0)
        self.progress_label.config(text="")
        self.log(f"✔️ 批量处理完成，已处理 {processed} 个文件")
    
    def _view_duplicates(self):
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        duplicate_log = self.db_manager.duplicate_log
        if not duplicate_log:
            messagebox.showinfo("提示", "暂无重复文件记录")
            return
        
        text = f"📋 重复文件记录 (共 {len(duplicate_log)} 个)\n" + "=" * 60 + "\n\n"
        for i, entry in enumerate(duplicate_log[-50:], 1):
            text += f"{i}. {entry['filename']}\n"
            text += f"   原始文件: {entry['original']}\n"
            text += f"   时间: {entry['timestamp'][:19]}\n\n"
        if len(duplicate_log) > 50:
            text += f"... 还有 {len(duplicate_log) - 50} 条记录\n"
        
        win = tk.Toplevel(self.root)
        win.title("重复文件记录")
        win.geometry("700x500")
        text_widget = scrolledtext.ScrolledText(win, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(1.0, text)
        text_widget.config(state=tk.DISABLED)
        
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text="📂 打开重复文件夹", 
                  command=lambda: os.startfile(self.engine.deduplicator.duplicate_folder)).pack(side=tk.LEFT, padx=2)
    
    def _show_stats(self):
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        stats = self.engine.get_stats()
        db_stats = stats.get('db_stats', {})
        
        text = "📊 统计信息\n" + "=" * 40 + "\n\n"
        text += f"📄 总处理文件: {stats['total_processed']}\n"
        text += f"🤖 自动分类: {stats['auto_classified']}\n"
        text += f"👤 手动分类: {stats['manual_classified']}\n"
        text += f"⏭️ 移至未分类: {stats['unknown_moved']}\n"
        text += f"🔄 重复文件: {stats['duplicates_found']}\n"
        text += f"❌ 错误: {stats['errors']}\n\n"
        
        text += "📚 数据库信息\n" + "-" * 40 + "\n"
        text += f"数据库路径: {db_stats.get('database_path', 'unknown')}\n"
        text += f"数据库版本: {db_stats.get('db_version', 'unknown')}\n"
        text += f"学习数据: {db_stats.get('total_learnings', 0)} 条\n"
        text += f"重复记录: {db_stats.get('total_duplicates', 0)} 条\n"
        text += f"哈希缓存: {db_stats.get('hash_cache_size', 0)} 个\n"
        created = db_stats.get('created_at')
        text += f"创建时间: {str(created)[:10] if created else 'unknown'}\n"
        updated = db_stats.get('last_updated')
        text += f"最后更新: {str(updated)[:10] if updated else 'unknown'}\n\n"
        
        text += "📁 文件夹信息\n" + "-" * 40 + "\n"
        text += f"未分类文件夹大小: {self._format_size(stats.get('unknown_folder_size', 0))}\n"
        text += f"重复文件夹: {stats.get('duplicate_folder', '')}\n"
        text += f"重复文件数: {stats.get('duplicate_count', 0)} 个\n"
        text += f"重复文件夹大小: {self._format_size(stats.get('duplicate_folder_size', 0))}\n"
        
        messagebox.showinfo("统计信息", text)
    
    def _format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    def _show_memory(self):
        memory = self.db_manager.memory
        if not memory:
            messagebox.showinfo("学习数据", "数据库中暂无学习数据")
            return
        
        text = f"📚 学习数据 (共 {len(memory)} 条)\n" + "=" * 60 + "\n\n"
        for i, item in enumerate(memory[-100:], 1):
            text += f"{i:3d}. {item['text']}\n"
            text += f"     └─> {item['path']}\n"
            if 'learned_at' in item:
                text += f"     学习时间: {item['learned_at'][:10]}\n"
            text += "\n"
        if len(memory) > 100:
            text += f"\n... 还有 {len(memory) - 100} 条数据未显示"
        
        win = tk.Toplevel(self.root)
        win.title(f"学习数据 ({len(memory)} 条)")
        win.geometry("650x450")
        text_widget = scrolledtext.ScrolledText(win, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(1.0, text)
        text_widget.config(state=tk.DISABLED)
    
    def _export_data(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if file_path:
            result = self.db_manager.export_learning_data(file_path)
            if result:
                messagebox.showinfo("成功", f"数据已导出到:\n{result}")
                self.log(f"📤 数据已导出: {result}")
            else:
                messagebox.showerror("错误", "数据导出失败")
    
    def _backup_data(self):
        backup_path = self.db_manager.create_backup()
        if backup_path:
            messagebox.showinfo("成功", f"数据已备份到:\n{backup_path}")
            self.log(f"💾 数据已备份: {backup_path}")
        else:
            messagebox.showerror("错误", "备份失败")
    
    def _clear_data(self):
        if messagebox.askyesno("⚠️ 确认清空", "确定要清空所有学习数据吗？此操作不可恢复！"):
            self.db_manager.clear_all_data()
            messagebox.showinfo("提示", "数据已清空")
            self._update_stats()
            self.log("🗑️ 数据已清空")
    
    def _update_stats(self):
        if self.engine:
            stats = self.engine.get_stats()
            self.stats_var.set(
                f"处理: {stats['total_processed']} | "
                f"自动: {stats['auto_classified']} | "
                f"手动: {stats['manual_classified']} | "
                f"未分类: {stats['unknown_moved']} | "
                f"重复: {stats['duplicates_found']}"
            )
            self.db_info_var.set(
                f"📊 学习: {len(self.db_manager.memory)} | "
                f"重复: {len(self.db_manager.duplicate_log)}"
            )


# ==================== 入口 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = SmartOrganizerApp(root)
    root.mainloop()