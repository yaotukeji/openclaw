import os
import json
from datetime import datetime
from typing import Dict, List, Optional

from src.common_utils.log_manager import get_logger
from src.common_utils.file_helper import FileHelper

logger = get_logger()


class ChangeAuditor:
    """变更审计记录器：统一封装所有变更写入方法，自动按目录归档任务、修复、提交记录"""

    def __init__(self):
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.CHANGES_DIR = os.path.join(project_root, ".harness", "changes")
        self.DAILY_LOG_DIR = os.path.join(self.CHANGES_DIR, "daily_task_log")
        self.REPAIR_RECORD_DIR = os.path.join(self.CHANGES_DIR, "repair_record")
        self.COMMIT_RECORD_DIR = os.path.join(self.CHANGES_DIR, "commit_record")
        for d in [self.DAILY_LOG_DIR, self.REPAIR_RECORD_DIR, self.COMMIT_RECORD_DIR]:
            os.makedirs(d, exist_ok=True)

    def _today_str(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def _now_str(self) -> str:
        return datetime.now().isoformat()

    def log_task(self, task_id: str, status: str, details: Dict):
        """记录单轮任务日志"""
        filepath = os.path.join(self.DAILY_LOG_DIR, f"task_{self._today_str()}.jsonl")
        record = {
            "timestamp": self._now_str(),
            "task_id": task_id,
            "status": status,
            "details": details
        }
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Task logged: {task_id} -> {status}")
        except Exception as e:
            logger.error(f"Failed to log task: {e}")

    def log_repair(self, issue_number: int, files: List[str], before: str, after: str, success: bool, reason: str = ""):
        """记录修复记录"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = {
            "timestamp": self._now_str(),
            "issue_number": issue_number,
            "modified_files": files,
            "before_state": before,
            "after_state": after,
            "success": success,
            "fail_reason": reason
        }
        filepath = os.path.join(self.REPAIR_RECORD_DIR, f"repair_{issue_number}_{timestamp}.json")
        try:
            FileHelper.write_file(filepath, json.dumps(record, indent=2, ensure_ascii=False))
            logger.info(f"Repair logged: issue #{issue_number} success={success}")
        except Exception as e:
            logger.error(f"Failed to log repair: {e}")

    def log_commit(self, issue_number: int, commit_hash: str, message: str, pushed: bool, branch: str):
        """记录提交记录"""
        record = {
            "timestamp": self._now_str(),
            "issue_number": issue_number,
            "commit_hash": commit_hash,
            "message": message,
            "pushed": pushed,
            "branch": branch
        }
        filepath = os.path.join(self.COMMIT_RECORD_DIR, f"commit_{self._today_str()}.jsonl")
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Commit logged: {commit_hash} pushed={pushed}")
        except Exception as e:
            logger.error(f"Failed to log commit: {e}")

    def update_global_summary(self, stats: Dict):
        """更新全局汇总统计表"""
        summary_path = os.path.join(self.CHANGES_DIR, "global_change_summary.md")
        try:
            lines = [
                "# 全局变更汇总统计表\n",
                "## 统计维度\n",
                f"- 总任务轮次: {stats.get('total_tasks', 0)}\n",
                f"- 总处理Issue数: {stats.get('total_issues', 0)}\n",
                f"- 成功修复数: {stats.get('success_repairs', 0)}\n",
                f"- 失败修复数: {stats.get('failed_repairs', 0)}\n",
                f"- 黑名单Issue数: {stats.get('blacklisted', 0)}\n",
                f"- 总提交次数: {stats.get('total_commits', 0)}\n",
                "\n## 最后更新时间\n",
                f"{self._now_str()}\n"
            ]
            FileHelper.write_file(summary_path, "".join(lines))
        except Exception as e:
            logger.error(f"Failed to update global summary: {e}")
