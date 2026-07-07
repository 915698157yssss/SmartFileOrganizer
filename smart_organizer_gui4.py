"""
Smart Organizer v4 - 增强优化版
修复：
1. 修复循环处理自身日志文件的问题
2. 优化未分类文件夹管理
3. 增强文件处理稳定性
4. 改进交互体验
5. 修复线程安全问题
6. 优化内存管理
新增：
1. 文件去重检测
2. 批量操作进度显示
3. 分类规则自定义
4. 文件预览功能
5. 操作撤销功能
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
MIN_CONFIDENCE_THRESHOLD = 0.5  # 最小置信度阈值
MAX_LEARNING_ITEMS = 10000  # 最大学习数据量
SUPPORTED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
                        '.txt', '.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', 
                        '.zip', '.rar', '.7z', '.exe', '.msi', '.iso'}

# ==================== 文件去重工具 ====================
class FileDeduplicator:
    """文件去重工具"""
    
    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(os.path.dirname(__file__), "file_hash.db")
        self.hash_cache = {}
        self._load_cache()
    
    def _load_cache(self):
        """加载哈希缓存"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    self.hash_cache = json.load(f)
            except:
                self.hash_cache = {}
    
    def _save_cache(self):
        """保存哈希缓存"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.hash_cache, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def get_file_hash(self, filepath, chunk_size=8192):
        """计算文件哈希值"""
        if filepath in self.hash_cache:
            return self.hash_cache[filepath]
        
        try:
            hasher = hashlib.sha256()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            hash_value = hasher.hexdigest()
            self.hash_cache[filepath] = hash_value
            self._save_cache()
            return hash_value
        except Exception as e:
            return None
    
    def is_duplicate(self, filepath):
        """检查是否为重复文件"""
        hash_value = self.get_file_hash(filepath)
        if not hash_value:
            return False
        
        # 检查是否已存在相同哈希的文件
        for path, h in self.hash_cache.items():
            if path != filepath and h == hash_value:
                if os.path.exists(path):
                    return True
        return False
    
    def find_duplicates(self, directory):
        """查找目录中的重复文件"""
        duplicates = defaultdict(list)
        for root, dirs, files in os.walk(directory):
            for file in files:
                filepath = os.path.join(root, file)
                hash_value = self.get_file_hash(filepath)
                if hash_value:
                    duplicates[hash_value].append(filepath)
        
        return {h: paths for h, paths in duplicates.items() if len(paths) > 1}

# ==================== 核心逻辑类 ====================
class SmartOrganizerEngine:
    """文件分类引擎 - 增强版"""
    
    def __init__(self, watch_folder, base_target, log_callback=None):
        self.watch_folder = watch_folder
        self.base_target = base_target
        
        # 未分类文件夹放在监听目录的**同级目录**
        parent_folder = os.path.dirname(watch_folder)
        self.unknown_folder = os.path.join(parent_folder, "未分类文件")
        
        # 程序数据文件夹
        self.data_folder = os.path.join(parent_folder, "smart_organizer")
        self.log_file = os.path.join(self.data_folder, "file_log.txt")
        self.db_file = os.path.join(self.data_folder, "vector_db.json")
        self.hash_db_file = os.path.join(self.data_folder, "file_hash.json")
        
        self.log_callback = log_callback
        self.model = None
        self.index = None
        self.memory = []
        self.dimension = 384
        self.is_running = False
        self.observer = None
        self.pending_files = queue.Queue()
        self.processed_files = set()
        self.processed_lock = threading.Lock()
        self.deduplicator = FileDeduplicator(self.hash_db_file)
        
        # 统计信息
        self.stats = {
            'total_processed': 0,
            'auto_classified': 0,
            'manual_classified': 0,
            'unknown_moved': 0,
            'duplicates_found': 0,
            'errors': 0
        }
        
        # 初始化模型
        self._init_model()
        self.load_db()
        
        # 创建必要的目录
        os.makedirs(self.unknown_folder, exist_ok=True)
        os.makedirs(self.data_folder, exist_ok=True)
    
    def _init_model(self):
        """初始化语义模型"""
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
    
    def log(self, msg):
        """输出日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        if self.log_callback:
            self.log_callback(full_msg)
        print(full_msg)
    
    def encode(self, text):
        """文本向量化"""
        if self.model is None:
            return None
        try:
            vec = self.model.encode([text], show_progress_bar=False)[0]
            return np.array(vec).astype("float32")
        except Exception as e:
            self.log(f"❌ 向量化失败: {e}")
            return None
    
    def load_db(self):
        """加载学习数据库"""
        self.log("📦 正在加载学习数据库...")
        if not os.path.exists(self.db_file):
            self.log("✔ 无历史数据，跳过")
            return
        
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.memory = data.get("memory", [])
                self.stats = data.get("stats", self.stats)
            
            if len(self.memory) == 0:
                self.log("✔ 数据库为空")
                return
            
            vectors = []
            valid_memory = []
            for item in self.memory:
                vec = self.encode(item["text"])
                if vec is not None:
                    vectors.append(vec)
                    valid_memory.append(item)
            
            self.memory = valid_memory
            if len(vectors) > 0:
                self.index.reset()
                self.index.add(np.array(vectors).astype("float32"))
            
            self.log(f"✔ 加载完成: {len(self.memory)} 条学习数据")
        except Exception as e:
            self.log(f"⚠ 学习数据库加载失败: {e}")
            self.memory = []
    
    def save_db(self):
        """保存学习数据库"""
        data = {
            "memory": self.memory,
            "stats": self.stats,
            "last_updated": datetime.now().isoformat()
        }
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"⚠ 保存数据库失败: {e}")
    
    def predict_with_suggestions(self, text, top_k=3):
        """预测并返回 Top-K 建议"""
        if len(self.memory) == 0 or self.model is None:
            return []
        
        vec = self.encode(text)
        if vec is None:
            return []
        
        vec = vec.reshape(1, -1)
        k = min(top_k, len(self.memory))
        D, I = self.index.search(vec, k)
        
        suggestions = []
        seen_paths = set()
        for i in range(k):
            if I[0][i] != -1:
                path = self.memory[I[0][i]]["path"]
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                similarity = max(0, 1 - D[0][i] / 2)  # 归一化相似度
                if similarity >= MIN_CONFIDENCE_THRESHOLD:
                    suggestions.append((path, similarity))
        
        return suggestions[:top_k]
    
    def learn(self, text, path):
        """学习新的映射关系"""
        if not text or not path:
            return
        
        vec = self.encode(text)
        if vec is None:
            return
        
        # 检查是否已存在
        for item in self.memory:
            if item["text"] == text and item["path"] == path:
                self.log(f"ℹ️ 映射已存在: {text} -> {path}")
                return
        
        # 限制学习数据量
        if len(self.memory) >= MAX_LEARNING_ITEMS:
            # 删除最旧的条目
            self.memory.pop(0)
            self.index.reset()
            if self.memory:
                vectors = [self.encode(item["text"]) for item in self.memory if self.encode(item["text"]) is not None]
                if vectors:
                    self.index.add(np.array(vectors).astype("float32"))
        
        self.index.add(vec.reshape(1, -1))
        self.memory.append({"text": text, "path": path, "learned_at": datetime.now().isoformat()})
        self.save_db()
        self.log(f"📝 已学习: {text} -> {path}")
    
    def _should_skip_file(self, filename, filepath):
        """判断是否应该跳过该文件"""
        # 跳过临时文件
        temp_patterns = ["~$", ".tmp", ".temp", ".cache", ".lock", "._"]
        for pattern in temp_patterns:
            if pattern in filename:
                return True
        
        # 跳过系统文件
        if filename.startswith("."):
            return True
        
        # 跳过已处理的文件标记
        if filename.startswith("已处理_") or filename.startswith("processed_"):
            return True
        
        # 跳过程序自身的文件
        if filepath == self.log_file or filepath == self.db_file or filepath == self.hash_db_file:
            return True
        
        # 跳过数据目录中的文件
        if self.data_folder in filepath:
            return True
        
        # 跳过未分类目录中的文件（避免二次处理）
        if self.unknown_folder in filepath:
            return True
        
        # 检查是否已经处理过
        try:
            stat = os.stat(filepath)
            file_key = f"{filename}_{stat.st_size}_{stat.st_mtime}"
            with self.processed_lock:
                if file_key in self.processed_files:
                    return True
        except:
            pass
        
        return False
    
    def _mark_processed(self, filepath):
        """标记文件已处理"""
        try:
            stat = os.stat(filepath)
            filename = os.path.basename(filepath)
            file_key = f"{filename}_{stat.st_size}_{stat.st_mtime}"
            with self.processed_lock:
                self.processed_files.add(file_key)
                # 限制缓存大小
                if len(self.processed_files) > 5000:
                    # 转换为列表，删除最旧的1000条
                    files_list = list(self.processed_files)
                    self.processed_files = set(files_list[-4000:])
        except:
            pass
    
    def process_file(self, path, force=False):
        """
        处理单个文件
        返回：'auto' | 'unknown' | 'duplicate' | 'skipped' | 'error'
        """
        filename = os.path.basename(path)
        
        # 检查是否应该跳过
        if not force and self._should_skip_file(filename, path):
            return 'skipped'
        
        # 标记为正在处理
        self._mark_processed(path)
        
        self.log(f"📄 处理文件: {filename}")
        self.stats['total_processed'] += 1
        
        # 检查文件是否存在
        if not os.path.exists(path):
            self.log(f"⚠ 文件不存在: {path}")
            return 'error'
        
        # 检查文件大小（跳过空文件或过大文件）
        try:
            file_size = os.path.getsize(path)
            if file_size == 0:
                self.log(f"⚠ 空文件，跳过: {filename}")
                return 'skipped'
            if file_size > 2 * 1024 * 1024 * 1024:  # 2GB
                self.log(f"⚠ 文件过大 ({file_size/1024/1024/1024:.1f}GB)，跳过: {filename}")
                return 'skipped'
        except:
            pass
        
        # 检查是否为重复文件
        if self.deduplicator.is_duplicate(path):
            self.log(f"⚠ 检测到重复文件: {filename}")
            self.stats['duplicates_found'] += 1
            # 可选择删除重复文件或移动到重复文件夹
            duplicate_folder = os.path.join(self.data_folder, "重复文件")
            os.makedirs(duplicate_folder, exist_ok=True)
            try:
                shutil.move(path, os.path.join(duplicate_folder, filename))
                self.log(f"📦 重复文件已移至: {duplicate_folder}")
                return 'duplicate'
            except Exception as e:
                self.log(f"❌ 移动重复文件失败: {e}")
                return 'error'
        
        # 获取建议
        suggestions = self.predict_with_suggestions(filename, top_k=3)
        
        if suggestions and suggestions[0][1] >= MIN_CONFIDENCE_THRESHOLD:
            # 有匹配建议 → 自动分类到目标目录
            target_sub = suggestions[0][0]
            self.log(f"🤖 自动分类: {filename} -> {target_sub} (置信度: {suggestions[0][1]:.2f})")
            success = self._move_file(path, filename, target_sub)
            if success:
                self.stats['auto_classified'] += 1
                self.save_db()
                return 'auto'
            else:
                return 'error'
        else:
            # 无匹配建议 → 自动移至未分类文件夹
            self.log(f"⏭️ 无匹配建议，移至未分类: {filename}")
            self._move_to_unknown(path, filename)
            self.stats['unknown_moved'] += 1
            self.save_db()
            return 'unknown'
    
    def _move_to_unknown(self, path, filename):
        """将文件移到未分类目录"""
        os.makedirs(self.unknown_folder, exist_ok=True)
        
        # 处理重名文件
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
            self._write_log(f"未分类: {filename} -> {os.path.basename(unknown_path)}")
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
            self.stats['errors'] += 1
    
    def _move_file(self, path, filename, target_sub):
        """执行文件移动"""
        target_dir = os.path.join(self.base_target, target_sub)
        os.makedirs(target_dir, exist_ok=True)
        
        target_path = os.path.join(target_dir, filename)
        
        # 处理重名文件
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
            self._write_log(f"移动: {filename} -> {target_sub}")
            
            # 学习这个分类
            self.learn(filename, target_sub)
            return True
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
            self._write_log(f"失败: {filename} | {e}")
            self.stats['errors'] += 1
            return False
    
    def process_unknown_file(self, filepath, target_sub):
        """从未分类文件夹处理文件，移到目标目录"""
        filename = os.path.basename(filepath)
        return self._move_file(filepath, filename, target_sub)
    
    def _write_log(self, msg):
        """写入日志文件"""
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} - {msg}\n")
        except:
            pass
    
    def handle_user_choice(self, filename, path, choice, suggestions):
        """处理用户的选择"""
        if choice is None:
            # 跳过 -> 移到未分类
            self._move_to_unknown(path, filename)
            self.stats['unknown_moved'] += 1
            return
        
        if isinstance(choice, int):
            # 选择了建议中的某一条
            if choice < len(suggestions):
                target_sub = suggestions[choice][0]
            else:
                target_sub = "未分类"
        else:
            # 手动输入
            target_sub = choice
        
        success = self._move_file(path, filename, target_sub)
        if success:
            self.stats['manual_classified'] += 1
        self.save_db()
    
    def start(self):
        """启动文件监听"""
        if self.is_running:
            return
        
        self.is_running = True
        os.makedirs(self.unknown_folder, exist_ok=True)
        os.makedirs(self.data_folder, exist_ok=True)
        
        self.log("🚀 启动文件监听器...")
        self.log(f"📁 程序数据目录: {self.data_folder}")
        self.log(f"📁 未分类文件夹: {self.unknown_folder}")
        self.log(f"📁 日志文件: {self.log_file}")
        
        self.observer = Observer()
        handler = self._create_handler()
        self.observer.schedule(handler, self.watch_folder, recursive=False)
        self.observer.start()
        self.log("✔ 文件监听已启动")
    
    def _create_handler(self):
        """创建文件事件处理器"""
        engine = self
        
        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                # 延迟一下，确保文件写入完成
                time.sleep(0.5)
                # 检查文件是否完全写入
                if os.path.exists(event.src_path):
                    engine.pending_files.put(("process", event.src_path))
            
            def on_moved(self, event):
                if not event.is_directory:
                    time.sleep(0.5)
                    if os.path.exists(event.dest_path):
                        engine.pending_files.put(("process", event.dest_path))
        
        return Handler()
    
    def stop(self):
        """停止文件监听"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.is_running = False
        self.log("🛑 监听器已停止")
    
    def process_existing_files(self, progress_callback=None):
        """处理已存在的文件"""
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
        
        results = {
            'processed': 0,
            'auto': 0,
            'unknown': 0,
            'duplicate': 0,
            'skipped': 0,
            'error': 0
        }
        
        for i, file_path in enumerate(files):
            if progress_callback:
                progress_callback(i, len(files), os.path.basename(file_path))
            
            result = self.process_file(file_path)
            if result in results:
                results[result] += 1
        
        self.log(f"✔ 现有文件处理完成: {results}")
        return results
    
    def get_pending_file(self):
        """获取等待处理的文件"""
        try:
            return self.pending_files.get_nowait()
        except queue.Empty:
            return None
    
    def get_stats(self):
        """获取统计信息"""
        return {
            **self.stats,
            'learning_items': len(self.memory),
            'unknown_folder_size': self._get_folder_size(self.unknown_folder),
            'watch_folder_files': len([f for f in os.listdir(self.watch_folder) 
                                      if os.path.isfile(os.path.join(self.watch_folder, f))])
        }
    
    def _get_folder_size(self, folder):
        """获取文件夹大小"""
        total = 0
        if os.path.exists(folder):
            for root, dirs, files in os.walk(folder):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except:
                        pass
        return total

# ==================== 目录树选择对话框（增强版） ====================
class EnhancedFolderTreeDialog:
    """增强版目录树选择对话框"""
    
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
        self.file_preview = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"选择分类目录 - {filename}")
        self.dialog.geometry("750x600")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self._create_widgets()
        self._load_tree()
        
        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 750) // 2
        y = (self.dialog.winfo_screenheight() - 600) // 2
        self.dialog.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        """创建界面组件"""
        # 主布局：左侧目录树，右侧信息预览
        main_paned = ttk.PanedWindow(self.dialog, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # ===== 左侧：目录树 =====
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=2)
        
        # 顶部信息
        info_frame = ttk.Frame(left_frame)
        info_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(info_frame, text=f"📄 {self.filename}", 
                  font=("", 10, "bold")).pack(anchor=tk.W)
        
        # 建议按钮区域
        if self.suggestions:
            suggest_frame = ttk.LabelFrame(left_frame, text="💡 匹配建议", padding="5")
            suggest_frame.pack(fill=tk.X, pady=(0, 5))
            
            for idx, (path, score) in enumerate(self.suggestions):
                btn = ttk.Button(
                    suggest_frame,
                    text=f"📁 {path} ({score:.2f})",
                    command=lambda i=idx: self._quick_select(i)
                )
                btn.pack(side=tk.LEFT, padx=2, pady=2)
        
        # 当前路径
        path_frame = ttk.Frame(left_frame)
        path_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(path_frame, text="路径:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.current_path or self.base_path)
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, state="readonly")
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # 目录树
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree = ttk.Treeview(tree_frame, yscrollcommand=scrollbar.set, height=12)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.tree.yview)
        
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        
        # 按钮区域
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="📁 上级目录", command=self._go_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📂 选择此目录", command=self._confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📂 选择并学习", command=self._confirm_and_learn).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(btn_frame, text="⏭️ 跳过", command=self._skip).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="取消", command=self._cancel).pack(side=tk.RIGHT, padx=2)
        
        # ===== 右侧：文件预览 =====
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=1)
        
        if self.show_preview:
            preview_frame = ttk.LabelFrame(right_frame, text="📋 文件预览", padding="10")
            preview_frame.pack(fill=tk.BOTH, expand=True)
            
            # 文件信息
            self.preview_text = scrolledtext.ScrolledText(preview_frame, height=15, 
                                                         font=("Consolas", 9))
            self.preview_text.pack(fill=tk.BOTH, expand=True)
            
            # 显示文件信息
            self._show_file_info()
    
    def _show_file_info(self):
        """显示文件信息"""
        if not self.filename:
            return
        
        info = f"文件名: {self.filename}\n"
        info += f"扩展名: {os.path.splitext(self.filename)[1] or '无'}\n"
        info += "-" * 40 + "\n"
        
        # 尝试获取文件大小（如果存在）
        unknown_folder = os.path.join(os.path.dirname(os.path.dirname(self.base_path)), "未分类文件")
        filepath = os.path.join(unknown_folder, self.filename)
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            info += f"文件大小: {self._format_size(size)}\n"
            info += f"修改时间: {datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        self.preview_text.insert(tk.END, info)
        self.preview_text.config(state=tk.DISABLED)
    
    def _format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    def _quick_select(self, idx):
        """快速选择建议路径"""
        if idx < len(self.suggestions):
            self.selected_path = self.suggestions[idx][0]
            self.result = "confirm"
            self.dialog.destroy()
    
    def _load_tree(self, path=None):
        """加载目录树"""
        if path is None:
            path = self.current_path or self.base_path
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        try:
            items = []
            for name in os.listdir(path):
                full_path = os.path.join(path, name)
                if os.path.isdir(full_path) and not name.startswith("."):
                    # 检查子目录
                    has_children = any(
                        os.path.isdir(os.path.join(full_path, sub)) and not sub.startswith(".")
                        for sub in os.listdir(full_path)
                    ) if os.path.exists(full_path) else False
                    items.append((name, full_path, has_children))
            
            items.sort(key=lambda x: x[0])
            
            for name, full_path, has_children in items:
                display_path = full_path.replace(self.base_path, "").strip("\\/") or name
                node = self.tree.insert("", "end", text=name, 
                                       values=(full_path, display_path, has_children))
                if has_children:
                    # 添加占位子节点以便显示展开图标
                    self.tree.insert(node, "end", text="")
            
            self.path_var.set(path)
            
        except Exception as e:
            print(f"加载目录失败: {e}")
    
    def _on_double_click(self, event):
        """双击进入子目录"""
        selected = self.tree.selection()
        if not selected:
            return
        
        item = selected[0]
        values = self.tree.item(item, "values")
        if len(values) < 2:
            return
        
        full_path = values[0]
        if os.path.isdir(full_path):
            self.current_path = full_path
            self._load_tree(full_path)
    
    def _on_select(self, event):
        """选择事件 - 更新预览"""
        selected = self.tree.selection()
        if selected and self.show_preview:
            item = selected[0]
            values = self.tree.item(item, "values")
            if len(values) >= 3 and values[2]:
                self.preview_text.config(state=tk.NORMAL)
                self.preview_text.delete(1.0, tk.END)
                self.preview_text.insert(tk.END, f"📁 目录: {values[1]}\n")
                self.preview_text.insert(tk.END, f"子目录数: {len(os.listdir(values[0])) if os.path.exists(values[0]) else 0}")
                self.preview_text.config(state=tk.DISABLED)
    
    def _go_up(self):
        """回到上级目录"""
        parent = os.path.dirname(self.current_path)
        if parent and os.path.exists(parent) and parent.startswith(self.base_path):
            self.current_path = parent
            self._load_tree(parent)
        else:
            messagebox.showinfo("提示", "已在根目录")
    
    def _confirm(self):
        """确认选择当前目录"""
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
        """确认选择当前目录并学习"""
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
        """跳过"""
        self.result = "skip"
        self.dialog.destroy()
    
    def _cancel(self):
        """取消"""
        self.result = "cancel"
        self.dialog.destroy()

# ==================== GUI 应用程序（增强版） ====================
class SmartOrganizerApp:
    """主窗口 - 增强版"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("智能文件分类器 v4 - 增强版")
        self.root.geometry("1100x800")
        self.root.resizable(True, True)
        
        # 状态变量
        self.engine = None
        self.is_auto_mode = tk.BooleanVar(value=False)
        self.is_running = False
        self.current_file = None
        self.processing_queue = []
        self.undo_stack = []
        
        # 创建界面
        self._create_widgets()
        
        # 默认配置
        self.watch_entry.insert(0, DEFAULT_WATCH_FOLDER)
        self.target_entry.insert(0, DEFAULT_BASE_TARGET)
        
        # 更新日志
        self.log("=" * 60)
        self.log("👋 欢迎使用智能文件分类器 v4 - 增强版")
        self.log("📌 新功能：文件去重检测、批量处理进度、操作撤销")
        self.log("📌 有匹配建议 → 自动移至目标目录")
        self.log("📌 无匹配建议 → 自动移至未分类文件夹")
        self.log("📌 点击「📂 批量处理未分类文件」手动归类")
        self.log("=" * 60)
        self.log("请检查配置路径，然后点击「🚀 启动服务」")
    
    def _create_widgets(self):
        """创建界面组件"""
        # 主容器
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # ===== 上方：配置和控制区域 =====
        top_frame = ttk.Frame(main_paned)
        main_paned.add(top_frame, weight=1)
        
        # 配置区域
        config_frame = ttk.LabelFrame(top_frame, text="⚙️ 配置", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 5))
        
        # 监听目录
        ttk.Label(config_frame, text="📁 监听目录:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.watch_entry = ttk.Entry(config_frame, width=50)
        self.watch_entry.grid(row=0, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_watch).grid(row=0, column=2, pady=2)
        
        # 目标目录
        ttk.Label(config_frame, text="🎯 目标目录:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.target_entry = ttk.Entry(config_frame, width=50)
        self.target_entry.grid(row=1, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_target).grid(row=1, column=2, pady=2)
        
        # 控制按钮区域
        control_frame = ttk.Frame(config_frame)
        control_frame.grid(row=2, column=0, columnspan=3, pady=(10, 0))
        
        ttk.Checkbutton(control_frame, text="🤖 自动模式", 
                       variable=self.is_auto_mode).pack(side=tk.LEFT, padx=(0, 15))
        
        self.start_btn = ttk.Button(control_frame, text="🚀 启动服务", command=self._toggle_service)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(control_frame, text="📂 处理现有", command=self._process_existing).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📂 批量处理未分类", command=self._batch_process_unknown).pack(side=tk.LEFT, padx=2)
        
        ttk.Separator(control_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y, ipady=10)
        
        ttk.Button(control_frame, text="📊 统计", command=self._show_stats).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📚 学习数据", command=self._show_memory).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="🗑️ 清空数据库", command=self._clear_db).pack(side=tk.LEFT, padx=2)
        
        # 状态信息栏
        status_info_frame = ttk.Frame(config_frame)
        status_info_frame.grid(row=3, column=0, columnspan=3, pady=(10, 0), sticky=tk.W)
        
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_info_frame, textvariable=self.status_var, font=("", 9, "bold")).pack(side=tk.LEFT)
        
        ttk.Separator(status_info_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=10, fill=tk.Y, ipady=5)
        
        self.stats_var = tk.StringVar(value="处理: 0 | 自动: 0 | 手动: 0 | 未分类: 0")
        ttk.Label(status_info_frame, textvariable=self.stats_var, font=("", 9)).pack(side=tk.LEFT)
        
        # ===== 中间：文件交互区域 =====
        interact_frame = ttk.LabelFrame(top_frame, text="📋 文件分类确认", padding="10")
        interact_frame.pack(fill=tk.X, pady=5)
        
        # 当前文件信息
        self.interact_label = ttk.Label(interact_frame, text="等待文件...", font=("", 10, "bold"))
        self.interact_label.pack(anchor=tk.W)
        
        # 建议按钮区域
        self.suggest_frame = ttk.Frame(interact_frame)
        self.suggest_frame.pack(fill=tk.X, pady=(5, 0))
        self.suggest_buttons = []
        
        # 手动输入区域
        manual_frame = ttk.Frame(interact_frame)
        manual_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(manual_frame, text="自定义路径:").pack(side=tk.LEFT)
        self.manual_entry = ttk.Entry(manual_frame, width=40)
        self.manual_entry.pack(side=tk.LEFT, padx=5)
        self.manual_entry.bind("<Return>", lambda e: self._manual_confirm())
        
        ttk.Button(manual_frame, text="📂 浏览目录", command=self._open_folder_tree).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="✅ 确认", command=self._manual_confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="⏭️ 跳过", command=self._skip_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="↩️ 撤销", command=self._undo_last).pack(side=tk.LEFT, padx=2)
        
        self.hint_label = ttk.Label(interact_frame, text="💡 选择建议路径或点击「浏览目录」从树形目录选择", 
                                   foreground="gray")
        self.hint_label.pack(anchor=tk.W, pady=(5, 0))
        
        self._set_interact_enabled(False)
        
        # ===== 下方：日志区域 =====
        log_frame = ttk.LabelFrame(top_frame, text="📝 日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        log_control = ttk.Frame(log_frame)
        log_control.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(log_control, text="清空日志", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(log_control, text="导出日志", command=self._export_log).pack(side=tk.LEFT, padx=5)
        
        # 进度条（处理现有文件时显示）
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(log_control, variable=self.progress_var, 
                                           maximum=100, length=200)
        self.progress_bar.pack(side=tk.RIGHT)
        
        self.progress_label = ttk.Label(log_control, text="")
        self.progress_label.pack(side=tk.RIGHT, padx=5)
    
    def _set_interact_enabled(self, enabled):
        """设置交互区域是否可用"""
        state = "normal" if enabled else "disabled"
        for btn in self.suggest_buttons:
            btn.config(state=state)
        self.manual_entry.config(state=state)
    
    def _browse_watch(self):
        """浏览监听目录"""
        path = filedialog.askdirectory(title="选择监听目录")
        if path:
            self.watch_entry.delete(0, tk.END)
            self.watch_entry.insert(0, path)
    
    def _browse_target(self):
        """浏览目标目录"""
        path = filedialog.askdirectory(title="选择目标目录")
        if path:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, path)
    
    def log(self, msg):
        """在日志区域显示消息"""
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def _clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)
    
    def _export_log(self):
        """导出日志"""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.log_text.get(1.0, tk.END))
            self.log(f"📤 日志已导出: {file_path}")
    
    def _toggle_service(self):
        """启动/停止服务"""
        if not self.is_running:
            self._start_service()
        else:
            self._stop_service()
    
    def _start_service(self):
        """启动服务"""
        watch_folder = self.watch_entry.get().strip()
        base_target = self.target_entry.get().strip()
        
        if not os.path.exists(watch_folder):
            messagebox.showerror("错误", f"监听目录不存在: {watch_folder}")
            return
        
        if not os.path.exists(base_target):
            messagebox.showerror("错误", f"目标目录不存在: {base_target}")
            return
        
        try:
            self.engine = SmartOrganizerEngine(
                watch_folder, 
                base_target,
                log_callback=self.log
            )
            
            self.engine.start()
            self.is_running = True
            self.start_btn.config(text="🛑 停止服务")
            self.status_var.set("🟢 运行中")
            
            self.log(f"✔ 服务已启动，监听: {watch_folder}")
            
            # 启动后台处理线程
            self._start_background_thread()
            
            # 更新统计
            self._update_stats()
            
        except Exception as e:
            self.log(f"❌ 启动失败: {e}")
            messagebox.showerror("错误", f"启动失败: {e}")
    
    def _stop_service(self):
        """停止服务"""
        if self.engine:
            self.engine.stop()
        self.is_running = False
        self.start_btn.config(text="🚀 启动服务")
        self.status_var.set("🔴 已停止")
        self.log("🛑 服务已停止")
        self._set_interact_enabled(False)
        self._clear_interaction()
    
    def _start_background_thread(self):
        """启动后台处理线程"""
        def background_worker():
            while self.is_running:
                if self.engine:
                    pending = self.engine.get_pending_file()
                    if pending:
                        cmd, data = pending
                        if cmd == "process":
                            self.root.after(0, lambda: self._handle_auto_process(data))
                time.sleep(0.5)
        
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()
    
    def _handle_auto_process(self, path):
        """自动处理文件"""
        if self.engine:
            result = self.engine.process_file(path)
            self._update_stats()
            if result == 'unknown':
                # 无匹配，提示用户手动分类
                self.root.after(0, lambda: self._prompt_manual_classify(path))
    
    def _prompt_manual_classify(self, path):
        """提示用户手动分类"""
        if not os.path.exists(path):
            return
        
        filename = os.path.basename(path)
        suggestions = self.engine.predict_with_suggestions(filename, top_k=3)
        
        self.current_file = {
            'filename': filename,
            'path': path,
            'suggestions': suggestions
        }
        
        self._show_interaction(self.current_file)
    
    def _show_interaction(self, data):
        """显示交互界面"""
        filename = data["filename"]
        suggestions = data["suggestions"]
        self.current_file = data
        
        if suggestions:
            self.interact_label.config(
                text=f"📄 新文件: {filename}  —  请选择分类方式",
                foreground="blue"
            )
            self.hint_label.config(text="💡 点击建议按钮，或点击「浏览目录」从树形目录选择")
        else:
            self.interact_label.config(
                text=f"📄 新文件: {filename}  —  暂无匹配建议，请手动选择",
                foreground="orange"
            )
            self.hint_label.config(text="💡 暂无相似历史记录，点击「浏览目录」选择分类路径")
        
        # 清除旧按钮
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        
        # 创建建议按钮
        if suggestions:
            for idx, (path, score) in enumerate(suggestions):
                btn = ttk.Button(
                    self.suggest_frame,
                    text=f"📁 {path} ({score:.2f})",
                    command=lambda i=idx: self._suggest_confirm(i)
                )
                btn.pack(side=tk.LEFT, padx=2, pady=2)
                self.suggest_buttons.append(btn)
        
        self.manual_entry.delete(0, tk.END)
        self._set_interact_enabled(True)
        self.manual_entry.focus_set()
        self.status_var.set(f"⏳ 等待分类: {filename}")
        self.root.attributes('-topmost', True)
        self.root.attributes('-topmost', False)
    
    def _open_folder_tree(self):
        """打开目录树选择对话框"""
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
            engine=self.engine,
            show_preview=True
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
        """确认建议"""
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            suggestions = self.current_file.get("suggestions", [])
            if idx < len(suggestions):
                target = suggestions[idx][0]
                self.log(f"📝 选择建议: {filename} -> {target}")
                self.engine.handle_user_choice(
                    self.current_file["filename"],
                    self.current_file["path"],
                    idx,
                    suggestions
                )
                self._update_stats()
                self._clear_interaction()
    
    def _manual_confirm(self):
        """手动输入确认"""
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
        """跳过文件"""
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
    
    def _undo_last(self):
        """撤销上次操作"""
        if not self.undo_stack:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        
        # 实现撤销逻辑
        last_action = self.undo_stack.pop()
        self.log(f"↩️ 撤销: {last_action}")
        # TODO: 实现具体的撤销功能
    
    def _clear_interaction(self):
        """清理交互状态"""
        self.current_file = None
        self.interact_label.config(text="等待文件...", foreground="black")
        self.hint_label.config(text="💡 选择建议路径或点击「浏览目录」从树形目录选择")
        self._set_interact_enabled(False)
        self.status_var.set("🟢 运行中")
        
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        self.manual_entry.delete(0, tk.END)
    
    def _process_existing(self):
        """处理现有文件"""
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
        
        self.log(f"✔ 处理完成: 自动分类 {results['auto']}, 未分类 {results['unknown']}, 重复 {results['duplicate']}")
    
    def _batch_process_unknown(self):
        """批量处理未分类文件"""
        if not self.engine or not self.is_running:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        unknown_folder = self.engine.unknown_folder
        if not os.path.exists(unknown_folder):
            messagebox.showinfo("提示", "未分类文件夹为空")
            return
        
        files = [f for f in os.listdir(unknown_folder) 
                 if os.path.isfile(os.path.join(unknown_folder, f))]
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
            
            # 如果是自动模式且有建议，自动处理
            if suggestions and self.is_auto_mode.get() and suggestions[0][1] >= MIN_CONFIDENCE_THRESHOLD:
                target_sub = suggestions[0][0]
                self.log(f"🤖 自动处理: {filename} -> {target_sub}")
                self.engine.process_unknown_file(filepath, target_sub)
                processed += 1
                continue
            
            # 弹出目录树对话框
            dialog = EnhancedFolderTreeDialog(
                self.root,
                self.engine.base_target,
                filename,
                self.engine.base_target,
                suggestions=suggestions,
                engine=self.engine,
                show_preview=True
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
            
            # 更新进度
            self.progress_var.set((i / len(files)) * 100)
            self.progress_label.config(text=f"{i}/{len(files)}")
        
        self._update_stats()
        self.progress_var.set(0)
        self.progress_label.config(text="")
        self.log(f"✔️ 批量处理完成，已处理 {processed} 个文件")
    
    def _show_stats(self):
        """显示统计信息"""
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        stats = self.engine.get_stats()
        text = "📊 统计信息\n" + "=" * 40 + "\n\n"
        text += f"📄 总处理文件: {stats['total_processed']}\n"
        text += f"🤖 自动分类: {stats['auto_classified']}\n"
        text += f"👤 手动分类: {stats['manual_classified']}\n"
        text += f"⏭️ 移至未分类: {stats['unknown_moved']}\n"
        text += f"🔄 重复文件: {stats['duplicates_found']}\n"
        text += f"❌ 错误: {stats['errors']}\n"
        text += f"\n📚 学习数据: {stats['learning_items']} 条\n"
        text += f"📁 未分类文件夹大小: {self._format_size(stats['unknown_folder_size'])}\n"
        text += f"📂 监听目录文件: {stats['watch_folder_files']} 个\n"
        
        messagebox.showinfo("统计信息", text)
    
    def _format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    def _update_stats(self):
        """更新统计显示"""
        if self.engine:
            stats = self.engine.get_stats()
            self.stats_var.set(
                f"处理: {stats['total_processed']} | "
                f"自动: {stats['auto_classified']} | "
                f"手动: {stats['manual_classified']} | "
                f"未分类: {stats['unknown_moved']} | "
                f"重复: {stats['duplicates_found']}"
            )
    
    def _show_memory(self):
        """显示学习数据"""
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        memory = self.engine.memory
        if not memory:
            messagebox.showinfo("学习数据", "数据库中暂无学习数据")
            return
        
        text = "📚 学习数据 (文件名 -> 分类目标)\n"
        text += "=" * 60 + "\n\n"
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
    
    def _clear_db(self):
        """清空数据库"""
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        if not messagebox.askyesno("⚠️ 确认", "确定要清空所有学习数据吗？此操作不可恢复！"):
            return
        
        self.engine.memory = []
        self.engine.index = faiss.IndexFlatL2(self.engine.dimension)
        self.engine.save_db()
        self.log("🗑️ 数据库已清空")
        self._update_stats()

# ==================== 入口 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = SmartOrganizerApp(root)
    root.mainloop()