import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional

from github import Github, GithubException

from src.common_utils.log_manager import get_logger
from src.common_utils.exception_catch import GitHubAPIException
from src.business_module.pr_tracker import PRTracker, PRRecord
from src.business_module.pr_analyzer import PRCloseAnalyzer

logger = get_logger()


class PRMonitor:
    """PR状态监控器：定期检查已提交PR的状态，分析关闭原因"""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.github: Optional[Github] = None
        self.tracker = PRTracker()
        self.analyzer = PRCloseAnalyzer()
        self._api_call_count = 0
        self._api_limit = 100  # 每轮监控的API调用上限

    def _init_github(self):
        """初始化GitHub连接"""
        if self.github is None:
            try:
                self.github = Github(self.access_token)
                user = self.github.get_user()
                _ = user.login
                logger.info("GitHub connection initialized for PR monitor")
            except Exception as e:
                logger.error(f"Failed to initialize GitHub: {e}")
                raise GitHubAPIException(f"GitHub init failed: {e}")

    def _rate_limited_call(self, func, *args, **kwargs):
        """带限流的API调用"""
        if self._api_call_count >= self._api_limit:
            logger.warning("PR monitor API call limit reached")
            raise GitHubAPIException("API call limit reached")
        time.sleep(1)  # 最小1秒间隔
        self._api_call_count += 1
        return func(*args, **kwargs)

    def _get_repo(self, owner: str, repo: str):
        """获取仓库实例"""
        self._init_github()
        try:
            return self._rate_limited_call(self.github.get_repo, f"{owner}/{repo}")
        except GithubException as e:
            logger.error(f"Failed to get repo {owner}/{repo}: {e}")
            return None

    def _get_pr_details(self, repo, pr_number: int) -> Optional[Dict]:
        """获取PR详细信息"""
        try:
            pr = self._rate_limited_call(repo.get_pull, pr_number)
            return {
                "number": pr.number,
                "title": pr.title,
                "body": pr.body or "",
                "state": pr.state,
                "merged": pr.merged,
                "merge_commit_sha": pr.merge_commit_sha,
                "created_at": pr.created_at.isoformat() if pr.created_at else "",
                "closed_at": pr.closed_at.isoformat() if pr.closed_at else "",
                "html_url": pr.html_url,
                "head_ref": pr.head.ref,
                "base_ref": pr.base.ref,
                "user_login": pr.user.login if pr.user else "",
                "labels": [label.name for label in pr.labels],
            }
        except Exception as e:
            logger.error(f"Failed to get PR #{pr_number}: {e}")
            return None

    def _get_pr_review_comments(self, repo, pr_number: int) -> List[Dict]:
        """获取PR的审查评论"""
        comments = []
        try:
            pr = self._rate_limited_call(repo.get_pull, pr_number)

            # 获取PR评论
            issue_comments = self._rate_limited_call(pr.get_issue_comments)
            for comment in issue_comments:
                comments.append({
                    "user": comment.user.login if comment.user else "",
                    "body": comment.body or "",
                    "created_at": comment.created_at.isoformat() if comment.created_at else "",
                })

            # 获取审查评论
            review_comments_list = self._rate_limited_call(pr.get_review_comments)
            for comment in review_comments_list:
                comments.append({
                    "user": comment.user.login if comment.user else "",
                    "body": comment.body or "",
                    "path": comment.path,
                    "line": comment.line,
                })

            # 获取审查（approval/request changes）
            reviews = self._rate_limited_call(pr.get_reviews)
            for review in reviews:
                if review.body:
                    comments.append({
                        "user": review.user.login if review.user else "",
                        "body": review.body,
                        "state": review.state,
                    })

        except Exception as e:
            logger.error(f"Failed to get comments for PR #{pr_number}: {e}")

        return comments

    def _get_ci_status(self, repo, pr_number: int) -> Optional[str]:
        """获取PR的CI状态"""
        try:
            pr = self._rate_limited_call(repo.get_pull, pr_number)
            # 获取最新的commit状态
            commits = self._rate_limited_call(pr.get_commits)
            if commits.totalCount > 0:
                latest_commit = commits[0]
                statuses = self._rate_limited_call(latest_commit.get_statuses)
                if statuses.totalCount > 0:
                    return statuses[0].state
                # 尝试获取checks
                check_suites = self._rate_limited_call(latest_commit.get_check_suites)
                if check_suites.totalCount > 0:
                    return check_suites[0].conclusion or check_suites[0].status
        except Exception as e:
            logger.error(f"Failed to get CI status for PR #{pr_number}: {e}")
        return None

    def check_single_pr(self, owner: str, repo: str, pr_number: int) -> Dict:
        """检查单个PR的状态并分析"""
        logger.info(f"Checking PR #{pr_number} in {owner}/{repo}")

        repository = self._get_repo(owner, repo)
        if not repository:
            return {"success": False, "error": "Failed to get repository"}

        # 获取PR详情
        pr_details = self._get_pr_details(repository, pr_number)
        if not pr_details:
            return {"success": False, "error": "Failed to get PR details"}

        current_status = pr_details["state"]
        is_merged = pr_details["merged"]

        # 更新数据库中的状态
        status_to_save = "merged" if is_merged else current_status
        self.tracker.update_pr_status(
            owner, repo, pr_number,
            status=status_to_save,
            closed_at=pr_details.get("closed_at"),
            merged=is_merged,
            merge_commit_sha=pr_details.get("merge_commit_sha"),
            labels=pr_details.get("labels", [])
        )

        # 如果PR已关闭或合并，进行分析
        if current_status in ("closed",) or is_merged:
            logger.info(f"PR #{pr_number} is {status_to_save}, analyzing...")

            # 获取评论和CI状态
            review_comments = self._get_pr_review_comments(repository, pr_number)
            ci_status = self._get_ci_status(repository, pr_number)

            # 更新评论和CI状态
            self.tracker.update_pr_status(
                owner, repo, pr_number,
                review_comments=review_comments,
                ci_status=ci_status
            )

            # 分析关闭原因
            analysis = self.analyzer.analyze_close_reason(
                pr_data=pr_details,
                review_comments=review_comments,
                issue_comments=[],  # PR评论已包含在review_comments中
                ci_status=ci_status
            )

            # 保存分析结果
            self.tracker.save_analysis(owner, repo, pr_number, analysis)

            logger.info(
                f"PR #{pr_number} analysis complete: "
                f"reason={analysis['primary_reason']}, "
                f"confidence={analysis['confidence']}"
            )

            return {
                "success": True,
                "pr_number": pr_number,
                "status": status_to_save,
                "analysis": analysis,
                "is_newly_closed": True
            }

        return {
            "success": True,
            "pr_number": pr_number,
            "status": current_status,
            "is_newly_closed": False
        }

    def check_all_open_prs(self) -> Dict:
        """检查所有记录中的open PR"""
        self._api_call_count = 0
        open_prs = self.tracker.get_open_prs()

        if not open_prs:
            logger.info("No open PRs to check")
            return {"success": True, "checked": 0, "newly_closed": 0, "results": []}

        logger.info(f"Checking {len(open_prs)} open PRs")
        results = []
        newly_closed_count = 0

        for pr_record in open_prs:
            try:
                owner = pr_record.get("upstream_owner")
                repo = pr_record.get("upstream_repo")
                pr_number = pr_record.get("pr_number")

                if not all([owner, repo, pr_number]):
                    logger.warning(f"Invalid PR record: {pr_record}")
                    continue

                result = self.check_single_pr(owner, repo, pr_number)
                results.append(result)

                if result.get("is_newly_closed"):
                    newly_closed_count += 1

            except Exception as e:
                logger.error(f"Error checking PR: {e}")
                results.append({"success": False, "error": str(e)})

        return {
            "success": True,
            "checked": len(open_prs),
            "newly_closed": newly_closed_count,
            "results": results
        }

    def generate_feedback_report(self) -> Dict:
        """生成反馈报告，包含所有已分析PR的改进建议"""
        closed_prs = self.tracker.get_closed_prs(unanalyzed_only=False)

        analyses = []
        for pr in closed_prs:
            if pr.get("analysis"):
                analyses.append(pr["analysis"])

        if not analyses:
            return {"success": True, "message": "No analyzed PRs yet", "rules": []}

        # 生成反馈规则
        rules = self.analyzer.generate_feedback_rules(analyses)

        # 生成报告
        report = {
            "generated_at": datetime.now().isoformat(),
            "total_analyzed": len(analyses),
            "total_rules_generated": len(rules),
            "rules": rules,
            "summary": self._generate_summary(analyses)
        }

        # 保存报告
        report_file = os.path.join(
            ".harness", "changes", "pr_feedback_report.json"
        )
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save feedback report: {e}")

        return report

    def _generate_summary(self, analyses: List[Dict]) -> Dict:
        """生成分析摘要"""
        reason_counts = {}
        priority_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        for analysis in analyses:
            primary = analysis.get("primary_reason", "unknown")
            reason_counts[primary] = reason_counts.get(primary, 0) + 1

            for improvement in analysis.get("improvements", []):
                priority = improvement.get("priority", "medium")
                priority_counts[priority] = priority_counts.get(priority, 0) + 1

        return {
            "primary_reason_distribution": reason_counts,
            "priority_distribution": priority_counts,
            "most_common_reason": max(reason_counts, key=reason_counts.get) if reason_counts else "none"
        }

    def get_stats(self) -> Dict:
        """获取监控统计"""
        return self.tracker.get_stats()
