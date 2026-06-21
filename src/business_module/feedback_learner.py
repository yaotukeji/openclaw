import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.common_utils.file_helper import FileHelper
from src.common_utils.log_manager import get_logger

logger = get_logger()


class FeedbackLearner:
    """反馈学习器：将PR关闭原因分析结果转化为可执行的改进规则"""

    RULES_DIR = os.path.join(".harness", "rules")
    FEEDBACK_RULES_FILE = os.path.join(RULES_DIR, "feedback_rules.json")
    LEARNED_PATTERNS_FILE = os.path.join(RULES_DIR, "learned_patterns.json")

    def __init__(self):
        os.makedirs(self.RULES_DIR, exist_ok=True)
        self._feedback_rules: Dict[str, Dict] = {}
        self._learned_patterns: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        """加载已有的反馈规则和学习模式"""
        if os.path.exists(self.FEEDBACK_RULES_FILE):
            try:
                content = FileHelper.read_file(self.FEEDBACK_RULES_FILE)
                self._feedback_rules = json.loads(content)
            except Exception as e:
                logger.error(f"Failed to load feedback rules: {e}")
                self._feedback_rules = {}

        if os.path.exists(self.LEARNED_PATTERNS_FILE):
            try:
                content = FileHelper.read_file(self.LEARNED_PATTERNS_FILE)
                self._learned_patterns = json.loads(content)
            except Exception as e:
                logger.error(f"Failed to load learned patterns: {e}")
                self._learned_patterns = {}

    def _save(self):
        """保存反馈规则和学习模式"""
        try:
            FileHelper.write_file(
                self.FEEDBACK_RULES_FILE,
                json.dumps(self._feedback_rules, indent=2, ensure_ascii=False)
            )
            FileHelper.write_file(
                self.LEARNED_PATTERNS_FILE,
                json.dumps(self._learned_patterns, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Failed to save feedback data: {e}")

    def learn_from_analysis(self, analysis: Dict, repo_config: Optional[Dict] = None):
        """
        从单个PR分析结果中学习

        Args:
            analysis: PR关闭原因分析结果
            repo_config: 仓库配置信息
        """
        primary_reason = analysis.get("primary_reason", "unknown")
        improvements = analysis.get("improvements", [])

        for improvement in improvements:
            action = improvement.get("action")
            if not action:
                continue

            # 更新反馈规则
            if action not in self._feedback_rules:
                self._feedback_rules[action] = {
                    "action": action,
                    "description": improvement.get("description", ""),
                    "priority": improvement.get("priority", "medium"),
                    "first_seen": datetime.now().isoformat(),
                    "occurrence_count": 0,
                    "trigger_reasons": set(),
                    "affected_repos": set(),
                    "enabled": True,
                    "implementation": None,
                }

            rule = self._feedback_rules[action]
            rule["occurrence_count"] += 1
            rule["last_seen"] = datetime.now().isoformat()
            rule["trigger_reasons"].add(primary_reason)

            if repo_config:
                repo_key = f"{repo_config.get('owner', '')}/{repo_config.get('repo', '')}"
                if repo_key:
                    rule["affected_repos"].add(repo_key)

            logger.info(
                f"Learned feedback rule: {action} "
                f"(count={rule['occurrence_count']}, priority={rule['priority']})"
            )

        # 保存更新
        self._save()

    def learn_from_analyses(self, analyses: List[Dict], repo_config: Optional[Dict] = None):
        """批量学习多个分析结果"""
        for analysis in analyses:
            self.learn_from_analysis(analysis, repo_config)

    def get_applicable_rules(self, repo_owner: str = "", repo_name: str = "") -> List[Dict]:
        """
        获取适用于当前仓库的反馈规则

        返回按优先级排序的规则列表
        """
        applicable = []
        repo_key = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else ""

        for action, rule in self._feedback_rules.items():
            if not rule.get("enabled", True):
                continue

            # 如果指定了仓库，优先返回该仓库相关的规则
            if repo_key:
                affected_repos = rule.get("affected_repos", set())
                if affected_repos and repo_key not in affected_repos:
                    continue

            applicable.append({
                "action": action,
                "description": rule.get("description", ""),
                "priority": rule.get("priority", "medium"),
                "occurrence_count": rule.get("occurrence_count", 0),
                "trigger_reasons": list(rule.get("trigger_reasons", set())),
            })

        # 按优先级排序
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        applicable.sort(key=lambda x: priority_order.get(x["priority"], 2))

        return applicable

    def get_pre_submit_checks(self, repo_owner: str = "", repo_name: str = "") -> List[str]:
        """
        获取提交前需要执行的额外检查

        根据学习到的反馈规则，返回需要在提交前执行的检查项
        """
        rules = self.get_applicable_rules(repo_owner, repo_name)
        checks = []

        for rule in rules:
            action = rule["action"]
            # 映射 action 到具体的检查项
            check_map = {
                "check_existing_fixes": "检查是否已有相关PR或修复",
                "better_issue_filtering": "过滤非Bug类Issue",
                "require_reproduction": "确保Issue包含复现步骤",
                "review_project_policy": "审查项目贡献政策",
                "auto_rebase": "自动rebase到最新分支",
                "enhance_pre_submit_checks": "运行完整的CI检查",
                "auto_format": "运行代码格式化工具",
                "require_tests": "为修复添加单元测试",
                "check_compatibility": "检查向后兼容性",
                "narrow_changes": "缩小修改范围",
            }
            check_name = check_map.get(action, action)
            checks.append(f"[{rule['priority'].upper()}] {check_name}")

        return checks

    def should_skip_issue(self, issue: Dict, repo_owner: str = "", repo_name: str = "") -> Tuple[bool, str]:
        """
        根据学习到的规则，判断是否应该跳过某个Issue

        Returns:
            (should_skip, reason)
        """
        rules = self.get_applicable_rules(repo_owner, repo_name)

        issue_title = (issue.get("title", "") + " " + issue.get("body", "")).lower()

        for rule in rules:
            action = rule["action"]

            # 根据规则类型进行判断
            if action == "better_issue_filtering":
                # 检查Issue是否包含足够的Bug描述
                bug_keywords = ["bug", "error", "exception", "crash", "fail", "broken"]
                if not any(kw in issue_title for kw in bug_keywords):
                    return True, f"Issue does not appear to be a bug report (learned from past PR rejections)"

            elif action == "require_reproduction":
                # 检查是否有复现步骤
                repro_keywords = ["reproduce", "reproduction", "steps", "example", "minimal"]
                if not any(kw in issue_title for kw in repro_keywords):
                    # 不直接跳过，但标记为低风险
                    pass

        return False, ""

    def get_repo_specific_config(self, repo_owner: str, repo_name: str) -> Dict:
        """
        获取针对特定仓库的学习配置

        例如：某些仓库特别重视测试覆盖，某些仓库对格式要求严格
        """
        repo_key = f"{repo_owner}/{repo_name}"
        config = {
            "extra_checks": [],
            "skip_checks": [],
            "warnings": [],
        }

        for action, rule in self._feedback_rules.items():
            if not rule.get("enabled", True):
                continue

            affected_repos = rule.get("affected_repos", set())
            if repo_key in affected_repos or not affected_repos:
                occurrence = rule.get("occurrence_count", 0)
                if occurrence >= 3:
                    config["warnings"].append({
                        "action": action,
                        "description": rule.get("description", ""),
                        "priority": rule.get("priority", "medium"),
                        "occurrence": occurrence,
                    })

        return config

    def export_rules_for_config(self) -> Dict:
        """
        导出规则用于更新 config.yaml

        返回可以直接合并到配置中的规则字典
        """
        rules = {
            "pr_feedback_rules": {},
            "pre_submit_checks": [],
            "skip_patterns": [],
        }

        for action, rule in self._feedback_rules.items():
            if not rule.get("enabled", True):
                continue

            occurrence = rule.get("occurrence_count", 0)
            if occurrence >= 2:  # 至少出现2次才纳入规则
                rules["pr_feedback_rules"][action] = {
                    "description": rule.get("description", ""),
                    "priority": rule.get("priority", "medium"),
                    "occurrence": occurrence,
                    "trigger_reasons": list(rule.get("trigger_reasons", set())),
                }

                # 生成预提交检查项
                if action in ["auto_format", "require_tests", "check_compatibility", "auto_rebase"]:
                    rules["pre_submit_checks"].append(action)

                # 生成跳过模式
                if action == "better_issue_filtering":
                    rules["skip_patterns"].append("non_bug_issues")

        return rules

    def reset_rules(self):
        """重置所有学习到的规则（谨慎使用）"""
        self._feedback_rules = {}
        self._learned_patterns = {}
        self._save()
        logger.info("All feedback rules have been reset")
