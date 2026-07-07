from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        print("检测到文件：", event.src_path)

path = r"F:\SmartFileOrganizer\待分类文件"

observer = Observer()
observer.schedule(Handler(), path, recursive=False)
observer.start()

print("开始监听...")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()

observer.join()