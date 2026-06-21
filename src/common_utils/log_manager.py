import logging
import os
import sys
from datetime import datetime


class LogManager:
    """统一格式日志输出、分级存储（无文件轮转，避免Windows文件占用问题）"""

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, log_dir: str = "logs", level: int = logging.DEBUG):
        if LogManager._initialized:
            return
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.log_dir = os.path.join(project_root, log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
        self.logger = logging.getLogger("github_auto_harness")
        self.logger.setLevel(level)
        self.logger.handlers = []
        self._setup_handlers()
        LogManager._initialized = True

    def _setup_handlers(self):
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 控制台输出 - Windows下设置UTF-8编码避免UnicodeEncodeError
        if sys.platform == "win32":
            import io
            # 强制使用UTF-8编码的输出流
            stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            console_handler = logging.StreamHandler(stdout)
        else:
            console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # 文件输出（按日期命名，不轮转）
        import time
        log_file = os.path.join(self.log_dir, f"app_{datetime.now().strftime('%Y%m%d')}_{int(time.time())}.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def debug(self, msg: str):
        self.logger.debug(msg)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def critical(self, msg: str):
        self.logger.critical(msg)


def get_logger() -> LogManager:
    return LogManager()
