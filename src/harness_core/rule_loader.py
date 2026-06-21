import os
import glob
from typing import Dict, Optional

from src.common_utils.log_manager import get_logger

logger = get_logger()


class RuleLoader:
    """规则加载器：遍历读取rules目录所有规则文档，缓存为内存字典"""

    def __init__(self):
        self._rules: Dict[str, str] = {}
        self._loaded = False
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.RULES_DIR = os.path.join(project_root, ".harness", "rules")

    def load_all_rules(self) -> Dict[str, str]:
        """批量加载全部约束"""
        if self._loaded:
            return self._rules

        if not os.path.exists(self.RULES_DIR):
            logger.error(f"Rules directory not found: {self.RULES_DIR}")
            return self._rules

        for filepath in glob.glob(os.path.join(self.RULES_DIR, "*.md")):
            name = os.path.basename(filepath)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    self._rules[name] = f.read()
                logger.info(f"Loaded rule: {name}")
            except Exception as e:
                logger.error(f"Failed to load rule {name}: {e}")

        self._loaded = True
        logger.info(f"Total rules loaded: {len(self._rules)}")
        return self._rules

    def get_rule_by_name(self, name: str) -> Optional[str]:
        """按规则名单独读取约束内容"""
        if not self._loaded:
            self.load_all_rules()
        return self._rules.get(name)

    def reload(self) -> Dict[str, str]:
        """重新加载所有规则"""
        self._rules.clear()
        self._loaded = False
        return self.load_all_rules()
