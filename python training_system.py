"""
学习数据库训练系统 v1.0
功能：
1. 扫描目标文件夹结构，自动生成分类路径数据
2. 分析文件名与路径的关联关系
3. 批量训练学习模型
4. 支持增量学习和数据验证
5. 导出训练好的数据库
6. 可视化训练进度和结果
"""

import os
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from datetime import datetime
from collections import defaultdict, Counter
import re
import hashlib
from pathlib import Path
import shutil

# ==================== 训练系统核心 ====================
class TrainingEngine:
    """学习数据库训练引擎"""
    
    def __init__(self, base_target_path, db_manager, log_callback=None):
        self.base_target_path = base_target_path
        self.db_manager = db_manager
        self.log_callback = log_callback
        self.is_training = False
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
    
    def scan_target_folder(self, progress_callback=None):
        """
        扫描目标文件夹，提取文件和路径信息
        返回: 学习数据列表
        """
        self.log("🔍 开始扫描目标文件夹...")
        self.log(f"📁 目标路径: {self.base_target_path}")
        
        if not os.path.exists(self.base_target_path):
            self.log("❌ 目标路径不存在")
            return []
        
        learning_data = []
        file_count = 0
        folder_count = 0
        
        # 遍历目标文件夹
        for root, dirs, files in os.walk(self.base_target_path):
            # 跳过隐藏文件夹
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            folder_count += 1
            
            # 获取相对路径作为分类目标
            relative_path = os.path.relpath(root, self.base_target_path)
            if relative_path == '.':
                relative_path = ''
            
            # 处理文件
            for file in files:
                # 跳过隐藏文件和临时文件
                if file.startswith('.') or file.startswith('~$'):
                    continue
                
                # 跳过系统文件
                if file.lower() in ['desktop.ini', 'thumbs.db', '.ds_store']:
                    continue
                
                file_path = os.path.join(root, file)
                try:
                    file_size = os.path.getsize(file_path)
                    # 跳过空文件
                    if file_size == 0:
                        continue
                except:
                    continue
                
                # 提取文件名（不含扩展名）作为学习文本
                file_name_without_ext = os.path.splitext(file)[0]
                
                # 如果文件名为空，跳过
                if not file_name_without_ext:
                    continue
                
                # 生成学习数据
                learning_item = {
                    'text': file_name_without_ext,
                    'path': relative_path if relative_path else '根目录',
                    'full_path': root,
                    'file_name': file,
                    'file_size': file_size,
                    'extension': os.path.splitext(file)[1].lower(),
                    'scanned_at': datetime.now().isoformat()
                }
                
                learning_data.append(learning_item)
                file_count += 1
                
                # 统计分类
                self.statistics['categories'][relative_path if relative_path else '根目录'] += 1
                
                # 更新进度
                if progress_callback and file_count % 100 == 0:
                    progress_callback(file_count, "扫描中...")
        
        self.statistics['total_files'] = file_count
        self.statistics['total_folders'] = folder_count
        
        self.log(f"✔ 扫描完成: 发现 {file_count} 个文件, {folder_count} 个文件夹")
        return learning_data
    
    def analyze_training_data(self, learning_data):
        """分析训练数据，提取关键词和模式"""
        self.log("📊 分析训练数据...")
        
        # 分析文件名关键词
        keyword_patterns = defaultdict(lambda: defaultdict(int))
        path_patterns = defaultdict(int)
        
        for item in learning_data:
            text = item['text']
            path = item['path']
            
            # 统计路径出现次数
            path_patterns[path] += 1
            
            # 提取关键词
            # 1. 按常见分隔符分词
            words = re.findall(r'[\u4e00-\u9fff\w]+', text)
            for word in words:
                if len(word) >= 2:  # 至少2个字符
                    keyword_patterns[word.lower()][path] += 1
            
            # 2. 提取数字年份模式
            year_match = re.search(r'(20\d{2})', text)
            if year_match:
                year = year_match.group(1)
                keyword_patterns[year][path] += 1
            
            # 3. 提取英文缩写模式（大写字母组合）
            abbrev_match = re.findall(r'[A-Z]{2,}', text)
            for abbr in abbrev_match:
                keyword_patterns[abbr.lower()][path] += 1
        
        self.log(f"✔ 分析完成: 发现 {len(keyword_patterns)} 个关键词模式, {len(path_patterns)} 个路径模式")
        
        return {
            'keyword_patterns': dict(keyword_patterns),
            'path_patterns': dict(path_patterns)
        }
    
    def train_model(self, learning_data, progress_callback=None):
        """
        训练学习模型
        将扫描到的数据添加到学习数据库中
        """
        self.is_training = True
        self.log("🧠 开始训练学习模型...")
        
        if not learning_data:
            self.log("❌ 没有可用的训练数据")
            self.is_training = False
            return False
        
        # 分析数据
        analysis = self.analyze_training_data(learning_data)
        
        # 合并到数据库
        total = len(learning_data)
        new_items = 0
        duplicate_items = 0
        
        for i, item in enumerate(learning_data):
            text = item['text']
            path = item['path']
            
            # 检查是否已存在
            exists = False
            for existing in self.db_manager.memory:
                if existing['text'] == text and existing['path'] == path:
                    exists = True
                    break
            
            if exists:
                duplicate_items += 1
                self.statistics['duplicates_found'] += 1
            else:
                # 添加新数据
                self.db_manager.memory.append({
                    'text': text,
                    'path': path,
                    'learned_at': datetime.now().isoformat(),
                    'source_file': item.get('file_name', ''),
                    'source_path': item.get('full_path', ''),
                    'extension': item.get('extension', '')
                })
                new_items += 1
                self.statistics['trained_items'] += 1
            
            # 更新进度
            if progress_callback and i % 50 == 0:
                progress_callback(i, total, f"训练中: {i}/{total}")
        
        # 限制数据量
        if len(self.db_manager.memory) > 10000:
            self.log("⚠️ 数据量超过限制，裁剪至10000条")
            self.db_manager.memory = self.db_manager.memory[-10000:]
        
        # 保存数据库
        self.db_manager.save_learning_data()
        
        self.log(f"✔ 训练完成: 新增 {new_items} 条, 跳过重复 {duplicate_items} 条")
        self.is_training = False
        return True
    
    def validate_training_data(self, learning_data):
        """验证训练数据质量"""
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
        
        # 检查数据质量
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
            
            # 检查重复
            key = f"{text}|{path}"
            if key in seen_entries:
                validation_result['statistics']['duplicate_entries'] += 1
            seen_entries.add(key)
            
            # 检查文件扩展名
            if extension in invalid_extensions:
                validation_result['statistics']['invalid_extension'] += 1
                validation_result['warnings'].append(f"跳过了 {extension} 扩展名的文件")
            
            # 检查文件名长度
            if len(text) < 2:
                validation_result['warnings'].append(f"文件名过短: {text}")
            
            # 检查文件大小
            if item.get('file_size', 0) < 1024:  # 小于1KB
                validation_result['statistics']['small_files'] += 1
        
        # 生成建议
        if validation_result['statistics']['total'] > 100:
            validation_result['statistics']['recommended_entries'] = min(
                validation_result['statistics']['total'], 1000
            )
        
        # 生成报告
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
        """生成训练报告"""
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
        sorted_categories = sorted(
            self.statistics['categories'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for category, count in sorted_categories[:10]:
            report += f"  {category}: {count}\n"
        
        if len(sorted_categories) > 10:
            report += f"  ... 还有 {len(sorted_categories) - 10} 个分类\n"
        
        report += f"\n📊 数据库状态:\n"
        report += f"  当前学习条目: {len(self.db_manager.memory)}\n"
        report += f"  数据库路径: {self.db_manager.base_path}\n"
        
        return report

# ==================== 训练系统GUI ====================
class TrainingSystemGUI:
    """学习数据库训练系统界面"""
    
    def __init__(self, parent, db_manager):
        self.parent = parent
        self.db_manager = db_manager
        self.training_engine = None
        self.is_training = False
        self.training_thread = None
        
        self._create_window()
        
    def _create_window(self):
        """创建训练系统窗口"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("学习数据库训练系统")
        self.window.geometry("900x700")
        self.window.transient(self.parent)
        
        # 主容器
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 配置区域
        config_frame = ttk.LabelFrame(main_frame, text="训练配置", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 目标路径
        ttk.Label(config_frame, text="📁 目标文件夹:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.target_path_var = tk.StringVar()
        self.target_entry = ttk.Entry(config_frame, textvariable=self.target_path_var, width=60)
        self.target_entry.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(config_frame, text="浏览", command=self._browse_target).grid(row=0, column=2, pady=5)
        
        # 训练选项
        options_frame = ttk.Frame(config_frame)
        options_frame.grid(row=1, column=0, columnspan=3, pady=10, sticky=tk.W)
        
        self.validate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="验证数据质量", variable=self.validate_var).pack(side=tk.LEFT, padx=5)
        
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="增量学习（跳过重复）", variable=self.incremental_var).pack(side=tk.LEFT, padx=5)
        
        self.auto_train_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="自动训练（扫描后立即训练）", variable=self.auto_train_var).pack(side=tk.LEFT, padx=5)
        
        # 控制按钮
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=5)
        
        self.scan_btn = ttk.Button(control_frame, text="🔍 扫描目标文件夹", command=self._scan_and_analyze)
        self.scan_btn.pack(side=tk.LEFT, padx=2)
        
        self.train_btn = ttk.Button(control_frame, text="🧠 开始训练", command=self._start_training, state=tk.DISABLED)
        self.train_btn.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(control_frame, text="📊 生成报告", command=self._show_report).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📤 导出数据库", command=self._export_database).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="📥 导入数据库", command=self._import_database).pack(side=tk.LEFT, padx=2)
        
        # 进度条
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X)
        
        self.progress_label = ttk.Label(progress_frame, text="就绪")
        self.progress_label.pack(anchor=tk.W, pady=2)
        
        # 信息显示
        info_frame = ttk.LabelFrame(main_frame, text="训练信息", padding="10")
        info_frame.pack(fill=tk.X, pady=5)
        
        self.info_text = tk.Text(info_frame, height=6, font=("Consolas", 9))
        self.info_text.pack(fill=tk.X)
        
        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="📝 训练日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 状态栏
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)
        
        # 初始显示数据库信息
        self._update_info()
        
    def _browse_target(self):
        """浏览目标文件夹"""
        path = filedialog.askdirectory(title="选择目标文件夹（包含已分类文件）")
        if path:
            self.target_path_var.set(path)
    
    def _log(self, msg):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.window.update_idletasks()
    
    def _update_info(self):
        """更新信息显示"""
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
        """扫描并分析目标文件夹"""
        target_path = self.target_path_var.get().strip()
        if not target_path:
            messagebox.showwarning("提示", "请选择目标文件夹")
            return
        
        if not os.path.exists(target_path):
            messagebox.showerror("错误", "目标文件夹不存在")
            return
        
        # 创建训练引擎
        self.training_engine = TrainingEngine(
            target_path, 
            self.db_manager,
            log_callback=self._log
        )
        
        self._log("=" * 50)
        self._log("🚀 开始扫描分析...")
        
        # 扫描
        self.scan_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="扫描中...")
        self.status_var.set("扫描中...")
        
        # 在后台线程中扫描
        def scan_thread():
            try:
                learning_data = self.training_engine.scan_target_folder(
                    progress_callback=self._update_scan_progress
                )
                
                # 存储扫描结果
                self.scanned_data = learning_data
                self.training_engine.training_data = learning_data
                
                # 分析数据
                if learning_data:
                    self._log(f"✔ 扫描完成: 发现 {len(learning_data)} 个有效文件")
                    
                    # 验证数据
                    if self.validate_var.get():
                        validation = self.training_engine.validate_training_data(learning_data)
                        self._log(f"📊 验证完成: {validation['statistics']['total']} 条数据")
                    
                    # 启用训练按钮
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
    
    def _update_scan_progress(self, count, status):
        """更新扫描进度"""
        self.window.after(0, lambda: self.progress_label.config(text=f"{status}: {count} 个文件"))
        self.window.after(0, lambda: self.progress_var.set(min(count % 100, 100)))
    
    def _start_training(self):
        """开始训练"""
        if not self.training_engine or not hasattr(self, 'scanned_data'):
            messagebox.showwarning("提示", "请先扫描目标文件夹")
            return
        
        if not self.scanned_data:
            messagebox.showwarning("提示", "没有可用的训练数据")
            return
        
        # 确认训练
        result = messagebox.askyesno(
            "确认训练",
            f"将添加 {len(self.scanned_data)} 条学习数据到数据库\n"
            f"当前数据库有 {len(self.db_manager.memory)} 条记录\n"
            "是否继续？"
        )
        
        if not result:
            return
        
        self._log("=" * 50)
        self._log("🧠 开始训练...")
        
        self.train_btn.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.status_var.set("训练中...")
        
        # 在后台线程中训练
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
        """更新训练进度"""
        progress = (current / total) * 100 if total > 0 else 0
        self.window.after(0, lambda: self.progress_var.set(progress))
        self.window.after(0, lambda: self.progress_label.config(text=f"{status}: {current}/{total}"))
    
    def _show_report(self):
        """显示训练报告"""
        if not self.training_engine:
            messagebox.showwarning("提示", "请先执行扫描或训练")
            return
        
        report = self.training_engine.generate_training_report()
        
        # 在新窗口中显示报告
        report_window = tk.Toplevel(self.window)
        report_window.title("训练报告")
        report_window.geometry("600x500")
        
        text_widget = scrolledtext.ScrolledText(report_window, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(1.0, report)
        text_widget.config(state=tk.DISABLED)
        
        # 保存报告按钮
        btn_frame = ttk.Frame(report_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        def save_report():
            file_path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
            )
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(report)
                messagebox.showinfo("成功", f"报告已保存到:\n{file_path}")
        
        ttk.Button(btn_frame, text="💾 保存报告", command=save_report).pack(side=tk.LEFT, padx=2)
    
    def _export_database(self):
        """导出数据库"""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")]
        )
        if file_path:
            result = self.db_manager.export_learning_data(file_path)
            if result:
                messagebox.showinfo("成功", f"数据库已导出到:\n{result}")
                self._log(f"📤 数据库已导出: {result}")
            else:
                messagebox.showerror("错误", "导出失败")
    
    def _import_database(self):
        """导入数据库"""
        file_path = filedialog.askopenfilename(
            title="选择数据文件",
            filetypes=[("JSON文件", "*.json"), ("数据库文件", "*.db"), ("所有文件", "*.*")]
        )
        if file_path:
            success, msg = self.db_manager.import_learning_data(file_path)
            if success:
                messagebox.showinfo("成功", msg)
                self._update_info()
                self._log(f"📥 数据库导入成功: {msg}")
            else:
                messagebox.showerror("错误", msg)
                self._log(f"❌ 数据库导入失败: {msg}")

# ==================== 集成到主程序 ====================
def integrate_training_system():
    """
    将训练系统集成到主程序的示例代码
    在主程序的菜单或按钮中添加以下调用
    """
    # 在主程序添加训练按钮的示例代码
    pass

# ==================== 独立运行入口 ====================
class StandaloneTrainingApp:
    """独立的训练系统应用"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("学习数据库训练系统 - 独立版")
        self.root.geometry("1000x750")
        
        # 初始化数据库管理器
        from smart_organizer_v5 import DatabaseManager
        self.db_manager = DatabaseManager()
        
        # 创建训练系统GUI
        self.training_gui = TrainingSystemGUI(self.root, self.db_manager)
        
        # 显示启动信息
        self.training_gui._log("=" * 50)
        self.training_gui._log("🚀 学习数据库训练系统 v1.0")
        self.training_gui._log(f"📁 数据库路径: {self.db_manager.base_path}")
        self.training_gui._log(f"📊 当前学习条目: {len(self.db_manager.memory)}")
        self.training_gui._log("=" * 50)
        self.training_gui._log("📌 使用说明:")
        self.training_gui._log("  1. 选择包含已分类文件的目标文件夹")
        self.training_gui._log("  2. 点击「扫描目标文件夹」分析文件结构")
        self.training_gui._log("  3. 点击「开始训练」将数据添加到学习数据库")
        self.training_gui._log("  4. 训练完成后数据将自动保存")

# ==================== 批量训练工具 ====================
class BatchTrainingTool:
    """批量训练工具 - 支持多个文件夹批量训练"""
    
    def __init__(self, db_manager, log_callback=None):
        self.db_manager = db_manager
        self.log_callback = log_callback
    
    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
    
    def batch_train(self, folder_list, progress_callback=None):
        """
        批量训练多个文件夹
        folder_list: 文件夹路径列表
        """
        self.log("=" * 50)
        self.log("🚀 开始批量训练...")
        
        results = {
            'total_folders': len(folder_list),
            'processed': 0,
            'total_files': 0,
            'new_items': 0,
            'errors': 0
        }
        
        for i, folder_path in enumerate(folder_list, 1):
            if not os.path.exists(folder_path):
                self.log(f"⚠️ 跳过不存在的文件夹: {folder_path}")
                results['errors'] += 1
                continue
            
            self.log(f"\n📂 [{i}/{len(folder_list)}] 训练: {folder_path}")
            
            # 创建训练引擎
            engine = TrainingEngine(folder_path, self.db_manager, self.log_callback)
            
            # 扫描
            learning_data = engine.scan_target_folder()
            if not learning_data:
                self.log(f"⚠️ 无有效数据: {folder_path}")
                continue
            
            # 训练
            success = engine.train_model(learning_data)
            if success:
                results['processed'] += 1
                results['total_files'] += len(learning_data)
                results['new_items'] += engine.statistics['trained_items']
                
                if progress_callback:
                    progress_callback(i, len(folder_list), folder_path)
            
            time.sleep(0.5)  # 避免资源竞争
        
        self.log("\n" + "=" * 50)
        self.log("📊 批量训练完成")
        self.log(f"  处理文件夹: {results['processed']}/{results['total_folders']}")
        self.log(f"  总文件数: {results['total_files']}")
        self.log(f"  新增条目: {results['new_items']}")
        self.log(f"  错误数: {results['errors']}")
        
        return results

# ==================== 主程序入口 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = StandaloneTrainingApp(root)
    root.mainloop()