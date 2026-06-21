import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from src.common_utils.log_manager import get_logger

logger = get_logger()


class PRCloseAnalyzer:
    """PR关闭原因分析器：分析PR被关闭或拒绝的原因，提取可改进点"""

    # 已知的PR关闭原因模式
    CLOSE_REASON_PATTERNS = {
        "duplicate": [
            r"duplicate\s+(?:of\s+)?#?\d+",
            r"duplicated?\s+(?:by|with|in)",
            r"already\s+(?:fixed|resolved|addressed)",
            r"superseded\s+by",
        ],
        "invalid": [
            r"not\s+(?:a\s+)?(?:bug|issue|problem)",
            r"invalid\s+(?:report|issue|pr)",
            r"works?\s+as\s+intended",
            r"expected\s+behavior",
            r"by\s+design",
        ],
        "insufficient": [
            r"need\s+(?:more\s+)?info(?:rmation)?",
            r"insufficient\s+(?:info|information|context|description)",
            r"please\s+provide\s+(?:more\s+)?details",
            r"reproduction\s+needed",
            r"cannot\s+reproduce",
            r"repro\s+needed",
        ],
        "rejected": [
            r"rejected",
            r"won'?t\s+(?:fix|merge|accept)",
            r"declined",
            r"not\s+(?:accept|merge|fix)ing",
        ],
        "conflict": [
            r"conflict",
            r"merge\s+conflict",
            r"outdated",
            r"stale",
        ],
        "ci_failed": [
            r"ci\s+(?:failed|failing|error)",
            r"tests?\s+(?:failed|failing)",
            r"build\s+(?:failed|failing)",
            r"check\s+(?:failed|failing)",
            r"workflow\s+(?:failed|failing)",
        ],
        "formatting": [
            r"format(?:ting)?\s+(?:issue|problem|error)",
            r"lint(?:ing)?\s+(?:failed|error|issue)",
            r"code\s+style",
            r"does\s+not\s+follow\s+(?:the\s+)?style",
        ],
        "coverage": [
            r"coverage\s+(?:decreased|too\s+low|missing)",
            r"test\s+coverage",
            r"missing\s+tests?",
            r"need\s+tests?",
        ],
        "breaking": [
            r"breaking\s+change",
            r"backward\s+(?:in)?compatible",
            r"api\s+break",
            r"regression",
        ],
        "scope": [
            r"out\s+of\s+scope",
            r"not\s+in\s+scope",
            r"unrelated\s+changes",
            r"too\s+(?:many|broad|large)\s+changes",
        ],
    }

    # 改进建议映射
    IMPROVEMENT_SUGGESTIONS = {
        "duplicate": {
            "action": "check_existing_fixes",
            "description": "在提交PR前检查是否已有相关PR或修复",
            "priority": "high",
        },
        "invalid": {
            "action": "better_issue_filtering",
            "description": "改进Issue筛选逻辑，过滤非Bug类Issue",
            "priority": "medium",
        },
        "insufficient": {
            "action": "require_reproduction",
            "description": "要求Issue包含复现步骤或最小示例",
            "priority": "high",
        },
        "rejected": {
            "action": "review_project_policy",
            "description": "审查项目贡献政策，确保修复符合项目方向",
            "priority": "medium",
        },
        "conflict": {
            "action": "auto_rebase",
            "description": "在提交前自动rebase到最新分支",
            "priority": "high",
        },
        "ci_failed": {
            "action": "enhance_pre_submit_checks",
            "description": "增强提交前的CI检查，确保本地通过后再提交",
            "priority": "critical",
        },
        "formatting": {
            "action": "auto_format",
            "description": "提交前自动运行代码格式化工具",
            "priority": "high",
        },
        "coverage": {
            "action": "require_tests",
            "description": "为修复添加对应的单元测试",
            "priority": "high",
        },
        "breaking": {
            "action": "check_compatibility",
            "description": "检查修复是否引入破坏性变更",
            "priority": "critical",
        },
        "scope": {
            "action": "narrow_changes",
            "description": "缩小修改范围，只包含必要的改动",
            "priority": "medium",
        },
    }

    def analyze_close_reason(
        self,
        pr_data: Dict,
        review_comments: List[Dict],
        issue_comments: List[Dict],
        ci_status: Optional[str] = None
    ) -> Dict:
        """
        分析PR被关闭的原因

        返回: {
            "primary_reason": str,
            "confidence": float,
            "detected_patterns": List[str],
            "all_text": str,
            "improvements": List[Dict],
            "detailed_analysis": str,
            "recommendations": List[str]
        }
        """
        # 收集所有相关文本
        all_text_parts = []

        # PR标题和正文
        all_text_parts.append(pr_data.get("title", ""))
        all_text_parts.append(pr_data.get("body", ""))

        # 审查评论
        for comment in review_comments:
            all_text_parts.append(comment.get("body", ""))

        # Issue评论
        for comment in issue_comments:
            all_text_parts.append(comment.get("body", ""))

        all_text = "\n".join(all_text_parts).lower()

        # 检测匹配的模式
        detected_patterns = []
        pattern_scores = {}

        for reason_type, patterns in self.CLOSE_REASON_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = re.findall(pattern, all_text, re.IGNORECASE)
                score += len(matches)
            if score > 0:
                detected_patterns.append(reason_type)
                pattern_scores[reason_type] = score

        # 考虑CI状态
        if ci_status in ("failure", "error", "failed"):
            if "ci_failed" not in detected_patterns:
                detected_patterns.append("ci_failed")
            pattern_scores["ci_failed"] = pattern_scores.get("ci_failed", 0) + 2

        # 确定主要原因
        if pattern_scores:
            primary_reason = max(pattern_scores, key=pattern_scores.get)
            confidence = min(1.0, pattern_scores[primary_reason] * 0.3 + 0.3)
        else:
            primary_reason = "unknown"
            confidence = 0.3

        # 生成改进建议
        improvements = []
        recommendations = []

        for reason in detected_patterns:
            suggestion = self.IMPROVEMENT_SUGGESTIONS.get(reason)
            if suggestion:
                improvements.append({
                    "reason": reason,
                    **suggestion
                })
                recommendations.append(
                    f"[{suggestion['priority'].upper()}] {suggestion['description']}"
                )

        # 生成详细分析
        detailed_analysis = self._generate_detailed_analysis(
            pr_data, primary_reason, detected_patterns, all_text
        )

        return {
            "primary_reason": primary_reason,
            "confidence": round(confidence, 2),
            "detected_patterns": detected_patterns,
            "pattern_scores": pattern_scores,
            "all_text_preview": all_text[:500] if all_text else "",
            "improvements": improvements,
            "detailed_analysis": detailed_analysis,
            "recommendations": recommendations,
            "analyzed_at": datetime.now().isoformat(),
        }

    def _generate_detailed_analysis(
        self,
        pr_data: Dict,
        primary_reason: str,
        detected_patterns: List[str],
        all_text: str
    ) -> str:
        """生成人类可读的详细分析报告"""
        lines = []
        lines.append(f"PR #{pr_data.get('number')} 关闭原因分析")
        lines.append("=" * 50)
        lines.append(f"主要关闭原因: {primary_reason}")
        lines.append(f"检测到的模式: {', '.join(detected_patterns)}")
        lines.append("")

        # 提取关键评论片段
        key_snippets = self._extract_key_snippets(all_text)
        if key_snippets:
            lines.append("关键评论片段:")
            for snippet in key_snippets[:5]:
                lines.append(f"  - {snippet}")
            lines.append("")

        # 生成改进建议
        lines.append("改进建议:")
        for reason in detected_patterns:
            suggestion = self.IMPROVEMENT_SUGGESTIONS.get(reason)
            if suggestion:
                lines.append(f"  [{suggestion['priority'].upper()}] {suggestion['description']}")
                lines.append(f"    对应行动: {suggestion['action']}")

        return "\n".join(lines)

    def _extract_key_snippets(self, text: str, max_length: int = 200) -> List[str]:
        """从文本中提取关键评论片段"""
        snippets = []
        sentences = re.split(r'[.!?\n]+', text)

        # 关键词权重
        keywords = [
            "reject", "decline", "won't", "cannot", "unable", "failed",
            "error", "issue", "problem", "conflict", "duplicate", "invalid",
            "insufficient", "missing", "need", "require", "please", "suggest"
        ]

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue
            score = sum(1 for kw in keywords if kw in sentence.lower())
            if score >= 2:
                snippet = sentence[:max_length] + "..." if len(sentence) > max_length else sentence
                snippets.append((score, snippet))

        # 按权重排序
        snippets.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in snippets]

    def generate_feedback_rules(self, analyses: List[Dict]) -> List[Dict]:
        """
        从多个PR分析中生成反馈规则

        返回规则列表，可用于更新规则库
        """
        rule_counter = {}

        for analysis in analyses:
            for improvement in analysis.get("improvements", []):
                action = improvement["action"]
                if action not in rule_counter:
                    rule_counter[action] = {
                        "count": 0,
                        "reasons": set(),
                        "description": improvement["description"],
                        "priority": improvement["priority"],
                    }
                rule_counter[action]["count"] += 1
                rule_counter[action]["reasons"].add(improvement["reason"])

        # 生成规则（出现次数>=2才生成规则）
        rules = []
        for action, data in rule_counter.items():
            if data["count"] >= 2:
                rules.append({
                    "rule_id": f"feedback_{action}",
                    "rule_type": "pr_feedback",
                    "action": action,
                    "description": data["description"],
                    "priority": data["priority"],
                    "occurrence_count": data["count"],
                    "trigger_reasons": list(data["reasons"]),
                    "created_at": datetime.now().isoformat(),
                    "enabled": True,
                })

        return rules
