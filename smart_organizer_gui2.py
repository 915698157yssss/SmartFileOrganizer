"""
Smart Organizer v3-lite - 优化版
文件结构：
├── 待分类文件/              # 监听目录（只放待分类文件）
├── 未分类文件/              # 同级目录（跳过的文件）
├── .smart_organizer/        # 程序文件目录（隐藏文件夹）
│   ├── vector_db.json       # 学习数据库
│   ├── file_log.txt         # 操作日志
│   └── config.json          # 配置文件
├── smart_organizer_gui.py   # 程序主文件
└── SmartOrganizer.exe       # 打包后的可执行文件
"""

import os
import sys
import time
import shutil
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
from datetime import datetime

# ==================== 配置区域 ====================
DEFAULT_WATCH_FOLDER = r"F:\SmartFileOrganizer\待分类文件"
DEFAULT_BASE_TARGET = r"G:\（2026.6.5备份）眉山苏伊士污水处理有限公司"

# ==================== 路径管理 ====================
def get_app_data_folder():
    """获取程序数据文件夹路径（与待分类目录同级）"""
    # 获取当前脚本所在目录
    if getattr(sys, 'frozen', False):
        # 打包成exe后的路径
        script_dir = os.path.dirname(sys.executable)
    else:
        # 开发环境路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 在脚本目录下创建 .smart_organizer 文件夹
    data_folder = os.path.join(script_dir, ".smart_organizer")
    os.makedirs(data_folder, exist_ok=True)
    return data_folder

def get_db_file(watch_folder):
    """获取数据库文件路径（基于监听目录生成唯一名称）"""
    data_folder = get_app_data_folder()
    # 使用监听目录的路径哈希作为文件名的一部分，避免不同目录冲突
    import hashlib
    folder_hash = hashlib.md5(watch_folder.encode()).hexdigest()[:8]
    return os.path.join(data_folder, f"vector_db_{folder_hash}.json")

def get_log_file(watch_folder):
    """获取日志文件路径"""
    data_folder = get_app_data_folder()
    import hashlib
    folder_hash = hashlib.md5(watch_folder.encode()).hexdigest()[:8]
    return os.path.join(data_folder, f"file_log_{folder_hash}.txt")

def get_unknown_folder(watch_folder):
    """获取未分类文件夹路径（与监听目录同级）"""
    parent_folder = os.path.dirname(watch_folder)
    return os.path.join(parent_folder, "未分类文件")

# ==================== 核心逻辑类 ====================
class SmartOrganizerEngine:
    """文件分类引擎"""
    
    def __init__(self, watch_folder, base_target, log_callback=None):
        self.watch_folder = watch_folder
        self.base_target = base_target
        
        # 未分类文件夹放在监听目录的**同级目录**
        self.unknown_folder = get_unknown_folder(watch_folder)
        
        # 数据库和日志文件放在程序数据目录
        self.db_file = get_db_file(watch_folder)
        self.log_file = get_log_file(watch_folder)
        
        self.log_callback = log_callback
        self.model = None
        self.index = None
        self.memory = []
        self.dimension = 384
        self.is_running = False
        self.observer = None
        self.pending_files = queue.Queue()
        
        # 记录已处理的文件，避免重复处理
        self.processed_files = set()
        self.processed_lock = threading.Lock()
        
        # 初始化模型
        self._init_model()
        # 加载数据库
        self.load_db()
    
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
                self.memory = json.load(f)
            
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
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(self.memory, f, ensure_ascii=False, indent=2)
    
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
        for i in range(k):
            if I[0][i] != -1:
                path = self.memory[I[0][i]]["path"]
                similarity = 1 / (1 + D[0][i])
                suggestions.append((path, similarity))
        
        return suggestions
    
    def learn(self, text, path):
        """学习新的映射关系"""
        vec = self.encode(text)
        if vec is None:
            return
        
        for item in self.memory:
            if item["text"] == text and item["path"] == path:
                self.log(f"ℹ️ 映射已存在: {text} -> {path}")
                return
        
        self.index.add(vec.reshape(1, -1))
        self.memory.append({"text": text, "path": path})
        self.save_db()
        self.log(f"📝 已学习: {text} -> {path}")
    
    def _should_skip_file(self, filename, filepath):
        """判断是否应该跳过该文件"""
        # 跳过临时文件
        if filename.startswith("~$") or filename.startswith("."):
            return True
        
        # 跳过已处理的文件标记
        if filename.startswith("已处理_"):
            return True
        
        # 跳过程序自身的文件（数据库和日志）
        if filepath == self.db_file:
            return True
        
        if filepath == self.log_file:
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
                if len(self.processed_files) > 1000:
                    self.processed_files.clear()
        except:
            pass
    
    def process_file(self, path, auto_mode=False):
        """处理单个文件，返回是否需要用户交互"""
        filename = os.path.basename(path)
        
        # 检查是否应该跳过
        if self._should_skip_file(filename, path):
            return None
        
        # 标记为正在处理
        self._mark_processed(path)
        
        self.log(f"📄 处理文件: {filename}")
        
        suggestions = self.predict_with_suggestions(filename, top_k=3)
        
        if suggestions and auto_mode:
            # 自动模式：使用最佳匹配
            target_sub = suggestions[0][0]
            self.log(f"🤖 自动分类: {filename} -> {target_sub}")
            return self._move_file(path, filename, target_sub)
        else:
            # 交互模式：返回建议等待用户选择
            return {
                "filename": filename,
                "path": path,
                "suggestions": suggestions,
                "auto_mode": auto_mode
            }
    
    def _move_to_unknown(self, path, filename):
        """将文件移到未分类目录（同级目录）"""
        os.makedirs(self.unknown_folder, exist_ok=True)
        unknown_path = os.path.join(self.unknown_folder, filename)
        try:
            shutil.move(path, unknown_path)
            self.log(f"⏭️ 已移至未分类: {filename}")
            self._write_log(f"未分类: {filename}")
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
    
    def _move_file(self, path, filename, target_sub):
        """执行文件移动"""
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
            self._write_log(f"移动: {filename} -> {target_sub}")
            
            # 学习这个分类
            exists = any(item["text"] == filename and item["path"] == target_sub 
                        for item in self.memory)
            if not exists:
                similar = any(item["path"] == target_sub for item in self.memory)
                if not similar:
                    self.learn(filename, target_sub)
                else:
                    self.log(f"ℹ️ 已存在 {target_sub} 的映射，不重复学习")
            return True
        except Exception as e:
            self.log(f"❌ 移动失败: {e}")
            self._write_log(f"失败: {filename} | {e}")
            return False
    
    def _write_log(self, msg):
        """写入日志文件"""
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now()} - {msg}\n")
        except:
            pass
    
    def handle_user_choice(self, filename, path, choice, suggestions):
        """处理用户的选择"""
        if choice is None:
            # 跳过 -> 移到未分类
            self._move_to_unknown(path, filename)
            return
        
        if isinstance(choice, int):
            # 选择了建议中的某一条
            target_sub = suggestions[choice][0]
        else:
            # 手动输入
            target_sub = choice
        
        self._move_file(path, filename, target_sub)
    
    def start(self):
        """启动文件监听"""
        if self.is_running:
            return
        
        self.is_running = True
        os.makedirs(self.unknown_folder, exist_ok=True)
        os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
        
        self.log("🚀 启动文件监听器...")
        self.log(f"📁 监听目录: {self.watch_folder}")
        self.log(f"📁 未分类文件夹: {self.unknown_folder}")
        self.log(f"📁 数据库文件: {self.db_file}")
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
                engine.pending_files.put(("process", event.src_path))
            
            def on_moved(self, event):
                if not event.is_directory:
                    time.sleep(0.5)
                    engine.pending_files.put(("process", event.dest_path))
        
        return Handler()
    
    def stop(self):
        """停止文件监听"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.is_running = False
        self.log("🛑 监听器已停止")
    
    def process_existing_files(self, auto_mode=False):
        """处理已存在的文件"""
        self.log("📂 检查现有文件...")
        files = []
        for f in os.listdir(self.watch_folder):
            file_path = os.path.join(self.watch_folder, f)
            if os.path.isfile(file_path):
                files.append(file_path)
        
        if files:
            self.log(f"发现 {len(files)} 个现有文件待处理")
            for file_path in files:
                result = self.process_file(file_path, auto_mode)
                if result and isinstance(result, dict):
                    # 需要交互，放入队列
                    self.pending_files.put(("interact", result))
            self.log("✔ 现有文件处理完成")
        else:
            self.log("✔ 没有需要处理的现有文件")
    
    def get_pending_file(self):
        """获取等待处理的文件"""
        try:
            return self.pending_files.get_nowait()
        except queue.Empty:
            return None


# ==================== 目录树选择对话框 ====================
class FolderTreeDialog:
    """目录树选择对话框 - 让用户通过树形目录选择目标路径"""
    
    def __init__(self, parent, base_path, filename, current_path=""):
        self.parent = parent
        self.base_path = base_path
        self.filename = filename
        self.current_path = current_path
        self.selected_path = None
        self.result = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"选择分类目录 - {filename}")
        self.dialog.geometry("650x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self._create_widgets()
        self._load_tree()
        
        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() - 650) // 2
        y = (self.dialog.winfo_screenheight() - 500) // 2
        self.dialog.geometry(f"+{x}+{y}")
    
    def _create_widgets(self):
        """创建界面组件"""
        # 顶部提示
        info_frame = ttk.Frame(self.dialog, padding="10")
        info_frame.pack(fill=tk.X)
        
        ttk.Label(info_frame, text=f"📄 为文件选择目标目录: {self.filename}", 
                  font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(info_frame, text="双击文件夹进入子目录，点击按钮确认选择", 
                  foreground="gray").pack(anchor=tk.W)
        
        # 当前路径显示
        path_frame = ttk.Frame(self.dialog, padding="5")
        path_frame.pack(fill=tk.X, padx=10)
        
        ttk.Label(path_frame, text="当前路径:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.current_path or self.base_path)
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, state="readonly")
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        # 目录树
        tree_frame = ttk.Frame(self.dialog, padding="10")
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree = ttk.Treeview(tree_frame, yscrollcommand=scrollbar.set, height=15)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.tree.yview)
        
        # 绑定双击事件
        self.tree.bind("<Double-1>", self._on_double_click)
        
        # 按钮区域
        btn_frame = ttk.Frame(self.dialog, padding="10")
        btn_frame.pack(fill=tk.X)
        
        # 左侧按钮组
        left_btn_frame = ttk.Frame(btn_frame)
        left_btn_frame.pack(side=tk.LEFT)
        
        ttk.Button(left_btn_frame, text="📁 上级目录", command=self._go_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="📂 选择此目录", command=self._confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="📂 选择并学习", command=self._confirm_and_learn).pack(side=tk.LEFT, padx=2)
        
        # 右侧按钮组
        right_btn_frame = ttk.Frame(btn_frame)
        right_btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(right_btn_frame, text="⏭️ 跳过", command=self._skip).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_btn_frame, text="取消", command=self._cancel).pack(side=tk.LEFT, padx=2)
    
    def _load_tree(self, path=None):
        """加载目录树"""
        if path is None:
            path = self.current_path or self.base_path
        
        # 清空树
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        try:
            # 获取当前目录下的所有子目录
            items = []
            for name in os.listdir(path):
                full_path = os.path.join(path, name)
                if os.path.isdir(full_path) and not name.startswith("."):
                    items.append((name, full_path))
            
            # 排序
            items.sort(key=lambda x: x[0])
            
            # 添加到树
            for name, full_path in items:
                # 检查是否有子目录
                try:
                    has_children = False
                    for sub in os.listdir(full_path):
                        if os.path.isdir(os.path.join(full_path, sub)) and not sub.startswith("."):
                            has_children = True
                            break
                except:
                    has_children = False
                
                self.tree.insert("", "end", text=name, values=(full_path,), 
                               open=False)
            
            # 更新当前路径显示
            self.path_var.set(path)
            
        except Exception as e:
            print(f"加载目录失败: {e}")
    
    def _on_double_click(self, event):
        """双击进入子目录"""
        selected = self.tree.selection()
        if not selected:
            return
        
        item = selected[0]
        full_path = self.tree.item(item, "values")[0]
        
        if os.path.isdir(full_path):
            self.current_path = full_path
            self._load_tree(full_path)
    
    def _go_up(self):
        """回到上级目录"""
        parent = os.path.dirname(self.current_path)
        if parent and os.path.exists(parent) and parent.startswith(self.base_path):
            self.current_path = parent
            self._load_tree(parent)
        else:
            messagebox.showinfo("提示", "已在根目录")
    
    def _confirm(self):
        """确认选择当前目录（不学习）"""
        selected = self.tree.selection()
        if selected:
            full_path = self.tree.item(selected[0], "values")[0]
            relative_path = full_path.replace(self.base_path, "").strip("\\/")
            self.selected_path = relative_path
            self.result = "confirm"
            self.dialog.destroy()
        else:
            messagebox.showwarning("提示", "请先点击选择一个目录")
    
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
            messagebox.showwarning("提示", "请先点击选择一个目录")
    
    def _skip(self):
        """跳过"""
        self.result = "skip"
        self.dialog.destroy()
    
    def _cancel(self):
        """取消"""
        self.result = "cancel"
        self.dialog.destroy()


# ==================== GUI 应用程序 ====================
class SmartOrganizerApp:
    """主窗口"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("智能文件分类器 v3-lite")
        self.root.geometry("950x750")
        self.root.resizable(True, True)
        
        # 状态变量
        self.engine = None
        self.is_auto_mode = tk.BooleanVar(value=False)
        self.is_running = False
        self.current_file = None
        
        # 创建界面
        self._create_widgets()
        
        # 默认配置
        self.watch_entry.insert(0, DEFAULT_WATCH_FOLDER)
        self.target_entry.insert(0, DEFAULT_BASE_TARGET)
        
        # 显示数据文件夹位置
        data_folder = get_app_data_folder()
        self.log(f"👋 欢迎使用智能文件分类器")
        self.log(f"📁 程序数据目录: {data_folder}")
        self.log(f"📌 数据库和日志将保存在数据目录中")
        self.log(f"📌 新文件进入待分类目录时会询问您如何分类")
        self.log(f"📌 点击「📂 浏览目录」可通过树形目录选择分类路径")
        self.log(f"📌 也可在输入框中手动输入分类路径")
        self.log("请检查配置路径，然后点击「启动服务」")
    
    def _create_widgets(self):
        """创建界面组件"""
        # 主容器
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ===== 配置区域 =====
        config_frame = ttk.LabelFrame(main_frame, text="配置", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 监听目录
        ttk.Label(config_frame, text="监听目录:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.watch_entry = ttk.Entry(config_frame, width=45)
        self.watch_entry.grid(row=0, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_watch).grid(row=0, column=2, pady=2)
        ttk.Label(config_frame, text="💡 未分类文件夹创建在同级目录", 
                 foreground="gray").grid(row=0, column=3, padx=(10, 0), pady=2)
        
        # 目标目录
        ttk.Label(config_frame, text="目标目录:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.target_entry = ttk.Entry(config_frame, width=45)
        self.target_entry.grid(row=1, column=1, padx=(5, 5), pady=2)
        ttk.Button(config_frame, text="浏览", command=self._browse_target).grid(row=1, column=2, pady=2)
        ttk.Label(config_frame, text="💡 分类后的文件存放根目录", 
                 foreground="gray").grid(row=1, column=3, padx=(10, 0), pady=2)
        
        # 控制按钮区域
        control_frame = ttk.Frame(config_frame)
        control_frame.grid(row=2, column=0, columnspan=4, pady=(10, 0))
        
        ttk.Checkbutton(control_frame, text="自动模式（无需交互，有匹配时自动分类）", 
                       variable=self.is_auto_mode).pack(side=tk.LEFT, padx=(0, 20))
        
        self.start_btn = ttk.Button(control_frame, text="🚀 启动服务", command=self._toggle_service)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(control_frame, text="📂 处理现有文件", command=self._process_existing).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="📊 查看学习数据", command=self._show_memory).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="🗑️ 清空数据库", command=self._clear_db).pack(side=tk.LEFT, padx=5)
        
        # ===== 文件处理区域（交互） =====
        interact_frame = ttk.LabelFrame(main_frame, text="📋 文件分类确认", padding="10")
        interact_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 当前文件信息
        self.interact_label = ttk.Label(interact_frame, text="等待文件...", font=("", 10, "bold"))
        self.interact_label.pack(anchor=tk.W)
        
        # 建议按钮区域
        self.suggest_frame = ttk.Frame(interact_frame)
        self.suggest_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.suggest_buttons = []
        
        # 手动输入区域 + 目录浏览按钮
        manual_frame = ttk.Frame(interact_frame)
        manual_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(manual_frame, text="自定义分类路径:").pack(side=tk.LEFT)
        self.manual_entry = ttk.Entry(manual_frame, width=45)
        self.manual_entry.pack(side=tk.LEFT, padx=5)
        self.manual_entry.bind("<Return>", lambda e: self._manual_confirm())
        
        # 目录浏览按钮
        ttk.Button(manual_frame, text="📂 浏览目录", 
                  command=self._open_folder_tree).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="✅ 确认分类", command=self._manual_confirm).pack(side=tk.LEFT, padx=2)
        ttk.Button(manual_frame, text="⏭️ 跳过（移至未分类）", command=self._skip_file).pack(side=tk.LEFT, padx=2)
        
        # 提示标签
        self.hint_label = ttk.Label(interact_frame, text="💡 选择建议路径，或点击「浏览目录」从树形目录选择，也可手动输入", 
                                   foreground="gray")
        self.hint_label.pack(anchor=tk.W, pady=(5, 0))
        
        # 禁用初始状态
        self._set_interact_enabled(False)
        
        # ===== 日志区域 =====
        log_frame = ttk.LabelFrame(main_frame, text="📝 日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 日志控制
        log_control = ttk.Frame(log_frame)
        log_control.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(log_control, text="清空日志", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(log_control, text="导出日志", command=self._export_log).pack(side=tk.LEFT, padx=5)
        
        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _set_interact_enabled(self, enabled):
        """设置交互区域是否可用"""
        state = "normal" if enabled else "disabled"
        for btn in self.suggest_buttons:
            btn.config(state=state)
        self.manual_entry.config(state=state)
    
    def _browse_watch(self):
        """浏览监听目录"""
        path = filedialog.askdirectory(title="选择监听目录（待分类文件存放处）")
        if path:
            self.watch_entry.delete(0, tk.END)
            self.watch_entry.insert(0, path)
    
    def _browse_target(self):
        """浏览目标目录"""
        path = filedialog.askdirectory(title="选择目标目录（分类后的文件存放根目录）")
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
            self.status_var.set("运行中 - 正在监听文件变化")
            
            self.log(f"✔ 服务已启动，监听: {watch_folder}")
            
            # 启动后台处理线程
            self._start_background_thread()
            
        except Exception as e:
            self.log(f"❌ 启动失败: {e}")
            messagebox.showerror("错误", f"启动失败: {e}")
    
    def _stop_service(self):
        """停止服务"""
        if self.engine:
            self.engine.stop()
        self.is_running = False
        self.start_btn.config(text="🚀 启动服务")
        self.status_var.set("已停止")
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
                            # 自动处理
                            self.root.after(0, lambda: self._handle_auto_process(data))
                        elif cmd == "interact":
                            # 需要交互
                            self.root.after(0, lambda: self._show_interaction(data))
                time.sleep(0.5)
        
        thread = threading.Thread(target=background_worker, daemon=True)
        thread.start()
    
    def _handle_auto_process(self, path):
        """自动处理文件"""
        if self.engine:
            self.engine.process_file(path, auto_mode=True)
    
    def _show_interaction(self, data):
        """显示交互界面 - 新文件进入时询问用户"""
        filename = data["filename"]
        suggestions = data["suggestions"]
        self.current_file = data
        
        # 更新提示
        if suggestions:
            self.interact_label.config(
                text=f"📄 新文件: {filename}  —  请选择分类方式",
                foreground="blue"
            )
            self.hint_label.config(text="💡 点击建议按钮，或点击「浏览目录」从树形目录选择，也可手动输入")
        else:
            self.interact_label.config(
                text=f"📄 新文件: {filename}  —  暂无匹配建议，请手动选择",
                foreground="orange"
            )
            self.hint_label.config(text="💡 暂无相似历史记录，点击「浏览目录」选择分类路径，或手动输入")
        
        # 清除旧按钮
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        
        # 创建建议按钮
        if suggestions:
            for idx, (path, score) in enumerate(suggestions):
                btn = ttk.Button(
                    self.suggest_frame,
                    text=f"📁 {path} (相似度: {score:.2f})",
                    command=lambda i=idx: self._suggest_confirm(i)
                )
                btn.pack(side=tk.LEFT, padx=2, pady=2)
                self.suggest_buttons.append(btn)
        
        # 清空手动输入框
        self.manual_entry.delete(0, tk.END)
        
        # 启用交互
        self._set_interact_enabled(True)
        self.manual_entry.focus_set()
        self.status_var.set(f"⏳ 等待用户分类: {filename}")
        
        # 窗口闪烁提示
        self.root.attributes('-topmost', True)
        self.root.attributes('-topmost', False)
    
    def _open_folder_tree(self):
        """打开目录树选择对话框"""
        if not self.engine:
            return
        
        base_path = self.engine.base_target
        filename = self.current_file["filename"] if self.current_file else ""
        
        # 获取当前输入框中的路径作为默认
        current_path = self.manual_entry.get().strip()
        if current_path and os.path.exists(os.path.join(base_path, current_path)):
            full_current = os.path.join(base_path, current_path)
        else:
            full_current = base_path
        
        # 创建目录树对话框
        dialog = FolderTreeDialog(self.root, base_path, filename, full_current)
        self.root.wait_window(dialog.dialog)
        
        if dialog.result == "confirm" or dialog.result == "learn":
            selected_path = dialog.selected_path
            if selected_path:
                # 更新输入框
                self.manual_entry.delete(0, tk.END)
                self.manual_entry.insert(0, selected_path)
                
                # 如果选择了"学习"模式，自动确认
                if dialog.result == "learn":
                    self.log(f"📝 用户从目录树选择并学习: {filename} -> {selected_path}")
                    self._manual_confirm()
                else:
                    self.log(f"📝 用户从目录树选择: {filename} -> {selected_path}")
                    # 不自动确认，让用户手动点击「确认分类」
                    self.status_var.set(f"已选择路径: {selected_path}，请点击「确认分类」")
        elif dialog.result == "skip":
            self._skip_file()
        elif dialog.result == "cancel":
            self.log(f"📝 用户取消目录选择")
    
    def _suggest_confirm(self, idx):
        """确认建议"""
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            target = self.current_file['suggestions'][idx][0]
            self.log(f"📝 用户选择建议: {filename} -> {target}")
            self.engine.handle_user_choice(
                self.current_file["filename"],
                self.current_file["path"],
                idx,
                self.current_file["suggestions"]
            )
            self._clear_interaction()
    
    def _manual_confirm(self):
        """手动输入确认"""
        target = self.manual_entry.get().strip()
        if not target:
            messagebox.showwarning("提示", "请输入分类路径（如：4-财务管理/2-公司报销/2026年/202606）\n或点击「浏览目录」选择")
            return
        
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            self.log(f"📝 用户手动分类: {filename} -> {target}")
            self.engine.handle_user_choice(
                self.current_file["filename"],
                self.current_file["path"],
                target,
                self.current_file["suggestions"]
            )
            self.manual_entry.delete(0, tk.END)
            self._clear_interaction()
    
    def _skip_file(self):
        """跳过文件（移至未分类）"""
        if self.current_file and self.engine:
            filename = self.current_file["filename"]
            self.log(f"⏭️ 用户跳过: {filename} -> 移至未分类")
            self.engine.handle_user_choice(
                self.current_file["filename"],
                self.current_file["path"],
                None,
                self.current_file["suggestions"]
            )
            self._clear_interaction()
    
    def _clear_interaction(self):
        """清理交互状态"""
        self.current_file = None
        self.interact_label.config(text="等待文件...", foreground="black")
        self.hint_label.config(text="💡 选择建议路径，或点击「浏览目录」从树形目录选择，也可手动输入")
        self._set_interact_enabled(False)
        self.status_var.set("运行中")
        
        for btn in self.suggest_buttons:
            btn.destroy()
        self.suggest_buttons.clear()
        self.manual_entry.delete(0, tk.END)
    
    def _process_existing(self):
        """处理现有文件"""
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        if not self.is_running:
            messagebox.showwarning("提示", "服务未运行，请先启动服务")
            return
        
        auto_mode = self.is_auto_mode.get()
        self.log(f"📂 开始处理现有文件... (模式: {'自动' if auto_mode else '交互'})")
        self.engine.process_existing_files(auto_mode=auto_mode)
    
    def _show_memory(self):
        """显示学习数据"""
        if not self.engine:
            messagebox.showwarning("提示", "请先启动服务")
            return
        
        memory = self.engine.memory
        if not memory:
            messagebox.showinfo("学习数据", "数据库中暂无学习数据")
            return
        
        text = "📚 学习数据 (文件名 -> 分类目标):\n"
        text += "=" * 60 + "\n\n"
        for i, item in enumerate(memory, 1):
            text += f"{i:3d}. {item['text']}\n"
            text += f"     └─> {item['path']}\n\n"
        
        # 在新窗口中显示
        win = tk.Toplevel(self.root)
        win.title("学习数据")
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


# ==================== 入口 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = SmartOrganizerApp(root)
    root.mainloop()