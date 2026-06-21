import os
import shutil
import hashlib
from datetime import datetime
from typing import List, Optional


class FileHelper:
    """文件备份、还原、遍历、读写"""

    @staticmethod
    def ensure_dir(path: str):
        os.makedirs(path, exist_ok=True)

    @staticmethod
    def read_file(path: str, encoding: str = "utf-8") -> str:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            return f.read()

    @staticmethod
    def write_file(path: str, content: str, encoding: str = "utf-8"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)

    @staticmethod
    def copy_file(src: str, dst: str):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    @staticmethod
    def backup_file(src: str, backup_dir: str) -> str:
        """备份单个文件，返回备份路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        rel_path = os.path.basename(src)
        backup_path = os.path.join(backup_dir, f"{timestamp}_{rel_path}")
        FileHelper.copy_file(src, backup_path)
        return backup_path

    @staticmethod
    def backup_directory(src_dir: str, backup_dir: str) -> str:
        """备份整个目录，返回备份路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = os.path.join(backup_dir, f"{timestamp}_backup")
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path)
        shutil.copytree(src_dir, backup_path)
        return backup_path

    @staticmethod
    def restore_file(backup_path: str, original_path: str):
        """从备份还原文件"""
        shutil.copy2(backup_path, original_path)

    @staticmethod
    def restore_directory(backup_path: str, original_dir: str):
        """从备份还原目录"""
        if os.path.exists(original_dir):
            shutil.rmtree(original_dir)
        shutil.copytree(backup_path, original_dir)

    @staticmethod
    def list_files(directory: str, extensions: Optional[List[str]] = None) -> List[str]:
        """遍历目录下所有文件，可选按扩展名过滤"""
        files = []
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                filepath = os.path.join(root, filename)
                if extensions is None or any(filepath.endswith(ext) for ext in extensions):
                    files.append(filepath)
        return files

    @staticmethod
    def file_hash(path: str) -> str:
        """计算文件MD5哈希"""
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def remove_old_files(directory: str, days: int):
        """清理指定天数前的文件"""
        if not os.path.exists(directory):
            return
        now = datetime.now().timestamp()
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                mtime = os.path.getmtime(filepath)
                if (now - mtime) > (days * 86400):
                    os.remove(filepath)
