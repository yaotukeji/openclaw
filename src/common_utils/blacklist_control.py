import os
import json
from typing import List, Dict
from datetime import datetime

from src.common_utils.file_helper import FileHelper


class BlacklistControl:
    """Issue黑名单新增、查询、判定"""

    def __init__(self):
        self.blacklist: Dict[str, dict] = {}
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.BLACKLIST_FILE = os.path.join(project_root, ".harness", "changes", "blacklist.json")
        self._load()

    def _load(self):
        if os.path.exists(self.BLACKLIST_FILE):
            try:
                content = FileHelper.read_file(self.BLACKLIST_FILE)
                self.blacklist = json.loads(content)
            except Exception:
                self.blacklist = {}
        else:
            self.blacklist = {}

    def _save(self):
        FileHelper.write_file(self.BLACKLIST_FILE, json.dumps(self.blacklist, indent=2, ensure_ascii=False))

    def is_blacklisted(self, issue_number: str) -> bool:
        return issue_number in self.blacklist

    def get_fail_count(self, issue_number: str) -> int:
        entry = self.blacklist.get(str(issue_number))
        return entry["fail_count"] if entry else 0

    def record_failure(self, issue_number: str, reason: str = ""):
        key = str(issue_number)
        if key not in self.blacklist:
            self.blacklist[key] = {
                "issue_number": issue_number,
                "fail_count": 0,
                "first_fail_time": datetime.now().isoformat(),
                "reasons": []
            }
        self.blacklist[key]["fail_count"] += 1
        self.blacklist[key]["last_fail_time"] = datetime.now().isoformat()
        if reason:
            self.blacklist[key]["reasons"].append(reason)
        self._save()

    def record_success(self, issue_number: str):
        key = str(issue_number)
        if key in self.blacklist:
            del self.blacklist[key]
            self._save()

    def list_blacklisted(self) -> List[dict]:
        return list(self.blacklist.values())

    def should_skip(self, issue_number: str, threshold: int = 3) -> bool:
        return self.get_fail_count(str(issue_number)) >= threshold
