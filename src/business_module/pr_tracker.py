import os
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from src.common_utils.file_helper import FileHelper
from src.common_utils.log_manager import get_logger

logger = get_logger()


@dataclass
class PRRecord:
    """PR记录数据结构"""
    pr_number: int
    pr_url: str
    branch_name: str
    issue_number: int
    upstream_owner: str
    upstream_repo: str
    fork_owner: str
    title: str
    status: str  # open, closed, merged
    created_at: str
    last_checked_at: str
    closed_at: Optional[str] = None
    closed_reason: Optional[str] = None
    merged: bool = False
    merge_commit_sha: Optional[str] = None
    review_comments: List[Dict] = None
    ci_status: Optional[str] = None
    labels: List[str] = None
    analysis: Optional[Dict] = None

    def __post_init__(self):
        if self.review_comments is None:
            self.review_comments = []
        if self.labels is None:
            self.labels = []


class PRTracker:
    """PR状态追踪器：记录和管理所有已提交的PR"""

    PR_DB_FILE = os.path.join(".harness", "changes", "pr_database.json")
    PR_ANALYSIS_DIR = os.path.join(".harness", "changes", "pr_analysis")

    def __init__(self):
        os.makedirs(self.PR_ANALYSIS_DIR, exist_ok=True)
        self._pr_db: Dict[str, Dict] = {}  # key: "owner/repo#number"
        self._load_db()

    def _load_db(self):
        """加载PR数据库"""
        if os.path.exists(self.PR_DB_FILE):
            try:
                content = FileHelper.read_file(self.PR_DB_FILE)
                self._pr_db = json.loads(content)
                logger.info(f"Loaded {len(self._pr_db)} PR records from database")
            except Exception as e:
                logger.error(f"Failed to load PR database: {e}")
                self._pr_db = {}
        else:
            self._pr_db = {}

    def _save_db(self):
        """保存PR数据库"""
        try:
            os.makedirs(os.path.dirname(self.PR_DB_FILE), exist_ok=True)
            FileHelper.write_file(self.PR_DB_FILE, json.dumps(self._pr_db, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save PR database: {e}")

    def _make_key(self, owner: str, repo: str, pr_number: int) -> str:
        """生成PR唯一标识键"""
        return f"{owner}/{repo}#{pr_number}"

    def record_pr(self, pr_record: PRRecord):
        """记录新提交的PR"""
        key = self._make_key(pr_record.upstream_owner, pr_record.upstream_repo, pr_record.pr_number)
        self._pr_db[key] = asdict(pr_record)
        self._save_db()
        logger.info(f"Recorded PR {key} with status {pr_record.status}")

    def update_pr_status(self, owner: str, repo: str, pr_number: int, **updates):
        """更新PR状态"""
        key = self._make_key(owner, repo, pr_number)
        if key in self._pr_db:
            self._pr_db[key].update(updates)
            self._pr_db[key]["last_checked_at"] = datetime.now().isoformat()
            self._save_db()
            logger.info(f"Updated PR {key}: {updates}")
        else:
            logger.warning(f"PR {key} not found in database, cannot update")

    def get_open_prs(self) -> List[Dict]:
        """获取所有状态为 open 的PR"""
        return [pr for pr in self._pr_db.values() if pr.get("status") == "open"]

    def get_closed_prs(self, unanalyzed_only: bool = False) -> List[Dict]:
        """获取所有已关闭的PR"""
        closed = [pr for pr in self._pr_db.values() if pr.get("status") in ("closed", "merged")]
        if unanalyzed_only:
            closed = [pr for pr in closed if not pr.get("analysis")]
        return closed

    def get_pr(self, owner: str, repo: str, pr_number: int) -> Optional[Dict]:
        """获取单个PR记录"""
        key = self._make_key(owner, repo, pr_number)
        return self._pr_db.get(key)

    def save_analysis(self, owner: str, repo: str, pr_number: int, analysis: Dict):
        """保存PR关闭原因分析"""
        key = self._make_key(owner, repo, pr_number)
        if key in self._pr_db:
            self._pr_db[key]["analysis"] = analysis
            self._save_db()

            # 同时保存详细分析报告到文件
            analysis_file = os.path.join(
                self.PR_ANALYSIS_DIR,
                f"{owner}_{repo}_pr{pr_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            try:
                FileHelper.write_file(analysis_file, json.dumps(analysis, indent=2, ensure_ascii=False))
            except Exception as e:
                logger.error(f"Failed to save analysis file: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取PR统计信息"""
        total = len(self._pr_db)
        open_count = len(self.get_open_prs())
        closed_count = len([p for p in self._pr_db.values() if p.get("status") == "closed"])
        merged_count = len([p for p in self._pr_db.values() if p.get("status") == "merged"])
        analyzed_count = len([p for p in self._pr_db.values() if p.get("analysis")])

        return {
            "total": total,
            "open": open_count,
            "closed": closed_count,
            "merged": merged_count,
            "analyzed": analyzed_count,
            "unanalyzed_closed": closed_count - analyzed_count
        }
