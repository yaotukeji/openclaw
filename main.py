#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub开源项目自动化拉取-Issue分析-智能修复-自动提交系统
程序入口 + Application Owner Agent调度总控
支持多仓库轮询
"""

import os
import sys
import time
import uuid
import yaml
import threading
from typing import Dict, Any, List

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.common_utils.log_manager import LogManager, get_logger
from src.common_utils.blacklist_control import BlacklistControl
from src.common_utils.exception_catch import HarnessException, ConfigException
from src.harness_core.rule_loader import RuleLoader
from src.harness_core.skill_dispatcher import SkillDispatcher
from src.harness_core.wiki_context_reader import WikiContextReader
from src.harness_core.change_auditor import ChangeAuditor


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    # 如果路径不存在，尝试相对于 main.py 的位置
    if not os.path.exists(path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, path)
    if not os.path.exists(path):
        raise ConfigException(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class RepoTaskRunner:
    """
    单个仓库的任务执行器
    支持两种模式：
    1. 标准模式：从Issue抓取 -> 分析 -> 修复 -> 提交PR
    2. Issue-PR模式：从Issue获取关联PR -> 分析PR问题 -> 修复 -> 提交PR
    """

    def __init__(self, repo_config: Dict[str, Any], global_config: Dict[str, Any],
                 skill_dispatcher: SkillDispatcher, wiki_reader: WikiContextReader,
                 auditor: ChangeAuditor, blacklist: BlacklistControl):
        self.repo_config = repo_config
        self.global_config = global_config
        self.skill_dispatcher = skill_dispatcher
        self.wiki_reader = wiki_reader
        self.auditor = auditor
        self.blacklist = blacklist
        self.logger = get_logger()

        # GitHub配置
        github_cfg = global_config.get("github", {})
        self.access_token = github_cfg.get("access_token", "")
        self.fork_owner = github_cfg.get("fork_owner", "")

        # 仓库配置
        self.owner = repo_config.get("owner", "")
        self.repo = repo_config.get("repo", "")
        self.branch = repo_config.get("branch", "main")
        self.enabled = repo_config.get("enabled", True)
        # 是否启用Issue-PR模式（从Issue中获取关联PR进行修复）
        self.issue_pr_mode = repo_config.get("issue_pr_mode", False)

        # 路径配置
        paths = global_config.get("paths", {})
        self.local_repo_base = paths.get("local_repo", "local_repo")
        self.temp_backup = paths.get("temp_backup", "temp_backup")
        self.logs_dir = paths.get("logs", "logs")

        # 任务配置
        task_cfg = global_config.get("task", {})
        self.interval = task_cfg.get("interval", 10)
        self.retry = task_cfg.get("retry", 3)
        self.timeout = task_cfg.get("timeout", 300)
        self.rules_cfg = global_config.get("rules", {})

        # 统计
        self.stats = {
            "total_tasks": 0,
            "total_issues": 0,
            "success_repairs": 0,
            "failed_repairs": 0,
            "blacklisted": 0,
            "total_commits": 0
        }

    @property
    def local_repo_path(self) -> str:
        """生成本地仓库路径：local_repo/{owner}/{repo}"""
        return os.path.join(self.local_repo_base, self.owner, self.repo)

    def run_single_round(self) -> bool:
        """执行单轮完整任务链路"""
        if not self.enabled:
            self.logger.info(f"Repo {self.owner}/{self.repo} is disabled, skipping")
            return True

        task_id = str(uuid.uuid4())[:8]
        self.stats["total_tasks"] += 1
        self.logger.info(f"--- Repo [{self.owner}/{self.repo}] Task Round [{task_id}] Start ---")

        # 1. 源码同步
        sync_result = self._skill_repo_sync(task_id)
        if not sync_result.get("success"):
            self.auditor.log_task(task_id, "FAILED", {"stage": "repo_sync", "repo": f"{self.owner}/{self.repo}", "error": sync_result.get("error", "")})
            self.logger.error(f"Task {task_id} aborted: repo sync failed")
            return False

        repo_path = self.local_repo_path
        work_branch = sync_result.get("work_branch", "")

        # 根据模式选择不同的处理流程
        if self.issue_pr_mode:
            # Issue-PR模式：从Issue获取关联PR，分析PR问题并修复
            return self._run_issue_pr_mode(task_id, repo_path, work_branch)
        else:
            # 标准模式：直接处理Issue
            return self._run_standard_mode(task_id, repo_path, work_branch)

    def _run_standard_mode(self, task_id: str, repo_path: str, work_branch: str) -> bool:
        """标准模式：从Issue抓取 -> 分析 -> 修复 -> 提交PR"""
        # 2. Issue抓取筛选
        issue_result = self._skill_issue_fetch(task_id)
        if not issue_result.get("success"):
            self.auditor.log_task(task_id, "FAILED", {"stage": "issue_fetch", "repo": f"{self.owner}/{self.repo}", "error": issue_result.get("error", "")})
            self.logger.error(f"Task {task_id} aborted: issue fetch failed")
            return False

        issues = issue_result.get("issues", [])
        if not issues:
            self.logger.info(f"Repo {self.owner}/{self.repo}: No issues to process, round complete")
            self.auditor.log_task(task_id, "COMPLETED_NO_ISSUES", {"repo": f"{self.owner}/{self.repo}"})
            return True

        self.stats["total_issues"] += len(issues)
        self.logger.info(f"Processing {len(issues)} issues for {self.owner}/{self.repo}")

        # 3. 项目规范分析
        spec_result = self._skill_spec_analyze(task_id, repo_path)
        spec = spec_result.get("spec", {}) if spec_result.get("success") else {}

        # 逐个处理Issue
        last_issue_success = True
        for issue in issues:
            if work_branch and not last_issue_success:
                self._ensure_clean_work_branch(repo_path, work_branch)
            last_issue_success = self._process_single_issue(task_id, issue, repo_path, spec, work_branch)

        # 更新全局汇总
        self.stats["blacklisted"] = len(self.blacklist.list_blacklisted())
        self.auditor.update_global_summary(self.stats)

        self.logger.info(f"--- Repo [{self.owner}/{self.repo}] Task Round [{task_id}] End ---")
        return True

    def _run_issue_pr_mode(self, task_id: str, repo_path: str, work_branch: str) -> bool:
        """Issue-PR模式：从Issue获取关联PR -> 分析PR问题 -> 修复 -> 提交PR"""
        self.logger.info(f"Running in Issue-PR mode for {self.owner}/{self.repo}")

        # 2. 获取带有关联PR的Issue
        issue_pr_result = self._skill_issue_pr_fetch(task_id)
        if not issue_pr_result.get("success"):
            self.auditor.log_task(task_id, "FAILED", {"stage": "issue_pr_fetch", "repo": f"{self.owner}/{self.repo}", "error": issue_pr_result.get("error", "")})
            self.logger.error(f"Task {task_id} aborted: issue-pr fetch failed")
            return False

        issues_with_prs = issue_pr_result.get("issues_with_prs", [])
        if not issues_with_prs:
            self.logger.info(f"Repo {self.owner}/{self.repo}: No issues with linked PRs to process, round complete")
            self.auditor.log_task(task_id, "COMPLETED_NO_ISSUE_PRS", {"repo": f"{self.owner}/{self.repo}"})
            return True

        self.stats["total_issues"] += len(issues_with_prs)
        self.logger.info(f"Processing {len(issues_with_prs)} issues with linked PRs for {self.owner}/{self.repo}")

        # 3. 项目规范分析
        spec_result = self._skill_spec_analyze(task_id, repo_path)
        spec = spec_result.get("spec", {}) if spec_result.get("success") else {}

        # 逐个处理Issue-PR
        last_issue_success = True
        for item in issues_with_prs:
            issue = item.get("issue", {})
            linked_prs = item.get("linked_prs", [])

            if work_branch and not last_issue_success:
                self._ensure_clean_work_branch(repo_path, work_branch)
            last_issue_success = self._process_single_issue_with_pr(task_id, issue, linked_prs, repo_path, spec, work_branch)

        # 更新全局汇总
        self.stats["blacklisted"] = len(self.blacklist.list_blacklisted())
        self.auditor.update_global_summary(self.stats)

        self.logger.info(f"--- Repo [{self.owner}/{self.repo}] Task Round [{task_id}] End ---")
        return True

    def _skill_repo_sync(self, task_id: str) -> Dict:
        """执行源码同步技能"""
        self.logger.info(f"[Skill] repo_sync starting for {self.owner}/{self.repo}...")
        for attempt in range(self.retry):
            result = self.skill_dispatcher.run_skill_task(
                "skill_repo_sync",
                owner=self.owner,
                repo=self.repo,
                branch=self.branch,
                local_path=self.local_repo_path
            )
            if result.get("success"):
                self.logger.info(f"[Skill] repo_sync success for {self.owner}/{self.repo}")
                return result
            self.logger.warning(f"[Skill] repo_sync attempt {attempt + 1} failed for {self.owner}/{self.repo}, retrying...")
            time.sleep(2)
        return result

    def _skill_issue_fetch(self, task_id: str) -> Dict:
        """执行Issue抓取技能"""
        self.logger.info(f"[Skill] issue_fetch starting for {self.owner}/{self.repo}...")
        result = self.skill_dispatcher.run_skill_task(
            "skill_issue_fetch",
            access_token=self.access_token,
            owner=self.owner,
            repo=self.repo,
            blacklist=self.blacklist,
            rules=self.rules_cfg
        )
        return result

    def _skill_issue_pr_fetch(self, task_id: str) -> Dict:
        """执行Issue-PR抓取技能"""
        self.logger.info(f"[Skill] issue_pr_fetch starting for {self.owner}/{self.repo}...")
        result = self.skill_dispatcher.run_skill_task(
            "skill_issue_pr_fetch",
            access_token=self.access_token,
            owner=self.owner,
            repo=self.repo,
            rules=self.rules_cfg
        )
        return result

    def _skill_spec_analyze(self, task_id: str, repo_path: str) -> Dict:
        """执行项目规范分析技能"""
        self.logger.info(f"[Skill] spec_analyze starting for {self.owner}/{self.repo}...")
        result = self.skill_dispatcher.run_skill_task(
            "skill_spec_analyze",
            project_path=repo_path
        )
        return result

    def _process_single_issue(self, task_id: str, issue: Dict, repo_path: str, spec: Dict, work_branch: str = "") -> bool:
        """处理单条Issue，返回是否成功"""
        issue_number = issue.get("number")
        self.logger.info(f"Processing issue #{issue_number} in {self.owner}/{self.repo}: {issue.get('title', '')}")

        # 4. 代码智能修复
        repair_result = self.skill_dispatcher.run_skill_task(
            "skill_code_repair",
            issue=issue,
            spec=spec,
            source_path=repo_path,
            backup_dir=self.temp_backup
        )

        if not repair_result.get("success"):
            fail_reason = repair_result.get("fail_reason", "unknown")
            self.logger.warning(f"Issue #{issue_number} repair failed: {fail_reason}")
            self.blacklist.record_failure(issue_number, fail_reason)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                [],
                before="",
                after="",
                success=False,
                reason=fail_reason
            )
            return False

        modified_files = repair_result.get("modified_files", [])
        self.logger.info(f"Issue #{issue_number} repaired files: {modified_files}")

        # 5. 提交合规校验
        commit_message = self._build_commit_message(issue)
        check_result = self.skill_dispatcher.run_skill_task(
            "skill_commit_check",
            modified_files=modified_files,
            project_path=repo_path,
            spec=spec,
            commit_message=commit_message
        )

        if not check_result.get("success"):
            details = check_result.get("details", [])
            self.logger.warning(f"Issue #{issue_number} check failed: {details}")
            self.blacklist.record_failure(issue_number, f"check_failed: {details}")
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="check_failed",
                success=False,
                reason=str(details)
            )
            return False

        # 5.5 Real Behavior Proof 验证
        backups = repair_result.get("backups", {})
        behavior_result = self.skill_dispatcher.run_skill_task(
            "skill_behavior_verify",
            modified_files=modified_files,
            project_path=repo_path,
            issue=issue,
            spec=spec,
            backups=backups
        )

        if not behavior_result.get("success"):
            details = behavior_result.get("details", [])
            self.logger.warning(f"Issue #{issue_number} behavior verify failed: {details}")
            self.blacklist.record_failure(issue_number, f"behavior_verify_failed: {details}")
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="behavior_verify_failed",
                success=False,
                reason=str(details)
            )
            return False

        self.logger.info(f"Issue #{issue_number} behavior verify passed: build={behavior_result.get('build_passed')}, tests={behavior_result.get('tests_passed')}, semantic={behavior_result.get('semantic_match')}, file_selection={behavior_result.get('file_selection')}, compilation={behavior_result.get('compilation_clean')}, format_check={behavior_result.get('format_check_passed')}, minimal_change={behavior_result.get('minimal_change')}")

        # 6. 远程提交推送（推送到fork的新分支）
        push_result = self.skill_dispatcher.run_skill_task(
            "skill_git_push",
            files=modified_files,
            issue_number=issue_number,
            message=commit_message,
            repo_path=repo_path,
            branch=self.branch,
            work_branch=work_branch,
            fork_owner=self.fork_owner,
            upstream_owner=self.owner,
            upstream_repo=self.repo,
            access_token=self.access_token,
            issue_title=issue.get("title", "")
        )

        if not (push_result.get("success") and push_result.get("pushed")):
            error = push_result.get("error", "push failed")
            self.logger.warning(f"Issue #{issue_number} push failed: {error}")
            self.blacklist.record_failure(issue_number, error)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="push_failed",
                success=False,
                reason=error
            )
            return False

        branch_name = push_result.get("branch_name", "")
        self.logger.info(f"Issue #{issue_number} pushed to fork branch: {branch_name}")

        # 7. 创建Pull Request（传入behavior_proof以生成完整的Real behavior proof）
        pr_result = self.skill_dispatcher.run_skill_task(
            "skill_create_pr",
            access_token=self.access_token,
            upstream_owner=self.owner,
            upstream_repo=self.repo,
            fork_owner=self.fork_owner,
            branch_name=branch_name,
            issue_number=issue_number,
            issue_title=issue.get("title", ""),
            issue_body=issue.get("body", ""),
            behavior_proof=behavior_result,
            modified_files=modified_files,
            base_branch=self.branch
        )

        if pr_result.get("success"):
            pr_url = pr_result.get('pr_url', '')
            pr_number = pr_result.get('pr_number', 0)
            self.logger.info(f"Issue #{issue_number} PR created: {pr_url}")
            self.stats["success_repairs"] += 1
            self.stats["total_commits"] += 1
            self.blacklist.record_success(issue_number)
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="buggy",
                after="fixed",
                success=True
            )
            self.auditor.log_commit(
                issue_number,
                push_result.get("commit_hash", ""),
                commit_message,
                True,
                branch_name
            )
            self.logger.info(f"PR #{pr_number} created successfully")
            return True
        else:
            error = pr_result.get("error", "pr creation failed")
            self.logger.warning(f"Issue #{issue_number} PR creation failed: {error}")
            self.blacklist.record_failure(issue_number, error)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="pushed",
                after="pr_failed",
                success=False,
                reason=error
            )
            return False

    def _process_single_issue_with_pr(self, task_id: str, issue: Dict, linked_prs: List[Dict], repo_path: str, spec: Dict, work_branch: str = "") -> bool:
        """处理带有关联PR的Issue，分析PR问题并修复"""
        issue_number = issue.get("number")
        self.logger.info(f"Processing issue #{issue_number} with {len(linked_prs)} linked PRs in {self.owner}/{self.repo}: {issue.get('title', '')}")

        # 分析PR内容，提取问题信息
        pr_info = linked_prs[0] if linked_prs else {}
        pr_number = pr_info.get("number", 0)
        pr_title = pr_info.get("title", "")
        pr_body = pr_info.get("body", "")
        changed_files = pr_info.get("changed_files", [])

        self.logger.info(f"Analyzing PR #{pr_number}: {pr_title}")
        self.logger.info(f"PR changed files: {[f['filename'] for f in changed_files]}")

        # 合并Issue和PR信息，用于修复
        combined_issue = dict(issue)
        combined_issue["pr_number"] = pr_number
        combined_issue["pr_title"] = pr_title
        combined_issue["pr_body"] = pr_body
        combined_issue["pr_changed_files"] = [f["filename"] for f in changed_files]

        # 4. 代码智能修复（基于PR的问题分析）
        repair_result = self.skill_dispatcher.run_skill_task(
            "skill_code_repair",
            issue=combined_issue,
            spec=spec,
            source_path=repo_path,
            backup_dir=self.temp_backup
        )

        if not repair_result.get("success"):
            fail_reason = repair_result.get("fail_reason", "unknown")
            self.logger.warning(f"Issue #{issue_number} (PR #{pr_number}) repair failed: {fail_reason}")
            self.blacklist.record_failure(issue_number, fail_reason)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                [],
                before="",
                after="",
                success=False,
                reason=fail_reason
            )
            return False

        modified_files = repair_result.get("modified_files", [])
        self.logger.info(f"Issue #{issue_number} (PR #{pr_number}) repaired files: {modified_files}")

        # 5. 提交合规校验
        commit_message = self._build_commit_message_for_pr(issue, pr_number)
        check_result = self.skill_dispatcher.run_skill_task(
            "skill_commit_check",
            modified_files=modified_files,
            project_path=repo_path,
            spec=spec,
            commit_message=commit_message
        )

        if not check_result.get("success"):
            details = check_result.get("details", [])
            self.logger.warning(f"Issue #{issue_number} (PR #{pr_number}) check failed: {details}")
            self.blacklist.record_failure(issue_number, f"check_failed: {details}")
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="check_failed",
                success=False,
                reason=str(details)
            )
            return False

        # 5.5 Real Behavior Proof 验证
        backups = repair_result.get("backups", {})
        behavior_result = self.skill_dispatcher.run_skill_task(
            "skill_behavior_verify",
            modified_files=modified_files,
            project_path=repo_path,
            issue=combined_issue,
            spec=spec,
            backups=backups
        )

        if not behavior_result.get("success"):
            details = behavior_result.get("details", [])
            self.logger.warning(f"Issue #{issue_number} (PR #{pr_number}) behavior verify failed: {details}")
            self.blacklist.record_failure(issue_number, f"behavior_verify_failed: {details}")
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="behavior_verify_failed",
                success=False,
                reason=str(details)
            )
            return False

        self.logger.info(f"Issue #{issue_number} (PR #{pr_number}) behavior verify passed: build={behavior_result.get('build_passed')}, tests={behavior_result.get('tests_passed')}, semantic={behavior_result.get('semantic_match')}, file_selection={behavior_result.get('file_selection')}, compilation={behavior_result.get('compilation_clean')}, format_check={behavior_result.get('format_check_passed')}, minimal_change={behavior_result.get('minimal_change')}")

        # 6. 远程提交推送（推送到fork的新分支）
        push_result = self.skill_dispatcher.run_skill_task(
            "skill_git_push",
            files=modified_files,
            issue_number=issue_number,
            message=commit_message,
            repo_path=repo_path,
            branch=self.branch,
            work_branch=work_branch,
            fork_owner=self.fork_owner,
            upstream_owner=self.owner,
            upstream_repo=self.repo,
            access_token=self.access_token,
            issue_title=issue.get("title", "")
        )

        if not (push_result.get("success") and push_result.get("pushed")):
            error = push_result.get("error", "push failed")
            self.logger.warning(f"Issue #{issue_number} (PR #{pr_number}) push failed: {error}")
            self.blacklist.record_failure(issue_number, error)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="repaired",
                after="push_failed",
                success=False,
                reason=error
            )
            return False

        branch_name = push_result.get("branch_name", "")
        self.logger.info(f"Issue #{issue_number} (PR #{pr_number}) pushed to fork branch: {branch_name}")

        # 7. 创建Pull Request（传入behavior_proof以生成完整的Real behavior proof）
        pr_result = self.skill_dispatcher.run_skill_task(
            "skill_create_pr",
            access_token=self.access_token,
            upstream_owner=self.owner,
            upstream_repo=self.repo,
            fork_owner=self.fork_owner,
            branch_name=branch_name,
            issue_number=issue_number,
            issue_title=issue.get("title", ""),
            issue_body=issue.get("body", ""),
            behavior_proof=behavior_result,
            modified_files=modified_files,
            base_branch=self.branch
        )

        if pr_result.get("success"):
            pr_url = pr_result.get('pr_url', '')
            new_pr_number = pr_result.get('pr_number', 0)
            self.logger.info(f"Issue #{issue_number} (PR #{pr_number}) new PR created: {pr_url}")
            self.stats["success_repairs"] += 1
            self.stats["total_commits"] += 1
            self.blacklist.record_success(issue_number)
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="buggy",
                after="fixed",
                success=True
            )
            self.auditor.log_commit(
                issue_number,
                push_result.get("commit_hash", ""),
                commit_message,
                True,
                branch_name
            )
            self.logger.info(f"PR #{new_pr_number} created successfully (based on PR #{pr_number})")
            return True
        else:
            error = pr_result.get("error", "pr creation failed")
            self.logger.warning(f"Issue #{issue_number} (PR #{pr_number}) PR creation failed: {error}")
            self.blacklist.record_failure(issue_number, error)
            self.stats["failed_repairs"] += 1
            self.auditor.log_repair(
                issue_number,
                modified_files,
                before="pushed",
                after="pr_failed",
                success=False,
                reason=error
            )
            return False

    def _build_commit_message(self, issue: Dict) -> str:
        """生成规范提交信息（遵循 Conventional Commits）"""
        number = issue.get("number", 0)
        title = issue.get("title", "Fix issue")
        title = title.strip().replace("\n", " ")[:50]
        return f"fix(issue): resolve #{number} {title}"

    def _build_commit_message_for_pr(self, issue: Dict, pr_number: int) -> str:
        """生成规范提交信息（基于PR的修复）"""
        number = issue.get("number", 0)
        title = issue.get("title", "Fix issue")
        title = title.strip().replace("\n", " ")[:50]
        return f"fix(issue): resolve #{number} (based on PR #{pr_number}) {title}"

    def _ensure_clean_work_branch(self, repo_path: str, work_branch: str):
        """确保工作分支干净：切换到工作分支并丢弃所有未提交的修改"""
        try:
            from src.common_utils.cmd_executor import CmdExecutor
            # 切换到工作分支
            CmdExecutor.git(["checkout", work_branch], cwd=repo_path)
            # 检查是否有未提交的修改
            rc, out, _ = CmdExecutor.git(["status", "--short"], cwd=repo_path)
            if rc == 0 and out.strip():
                # 只有存在未提交修改时才清理
                self.logger.info(f"Work branch {work_branch} has uncommitted changes, cleaning...")
                CmdExecutor.git(["reset", "--hard", "HEAD"], cwd=repo_path)
                CmdExecutor.git(["clean", "-fd"], cwd=repo_path)
                self.logger.info(f"Work branch {work_branch} cleaned and ready")
            else:
                self.logger.info(f"Work branch {work_branch} is already clean")
        except Exception as e:
            self.logger.warning(f"Failed to clean work branch {work_branch}: {e}")


class InteractionTaskRunner:
    """
    互动任务执行器
    每小时自动扫描指定仓库的Issue和PR，进行有针对性的评论互动
    """

    def __init__(self, config: Dict[str, Any], skill_dispatcher: SkillDispatcher):
        self.config = config
        self.skill_dispatcher = skill_dispatcher
        self.logger = get_logger()

        # GitHub配置
        github_cfg = config.get("github", {})
        self.access_token = github_cfg.get("access_token", "")

        # 互动配置
        self.interaction_cfg = config.get("interaction", {})
        self.enabled = self.interaction_cfg.get("enabled", False)
        self.interval_hours = self.interaction_cfg.get("interval_hours", 1)
        self.interaction_types = self.interaction_cfg.get("interaction_types", ["issue", "pr"])
        self.max_interactions_per_repo = self.interaction_cfg.get("max_interactions_per_repo", 3)
        self.skip_draft_prs = self.interaction_cfg.get("skip_draft_prs", True)
        self.dedup_hours = self.interaction_cfg.get("dedup_hours", 24)

        # 目标仓库
        self.target_repos = self._get_target_repos()

        # 规则配置
        self.rules_cfg = config.get("rules", {})

        # 统计
        self.stats = {
            "total_interactions": 0,
            "issue_interactions": 0,
            "pr_interactions": 0,
            "failed_interactions": 0
        }

    def _get_target_repos(self) -> List[Dict[str, str]]:
        """获取目标仓库列表"""
        target_repos = self.interaction_cfg.get("target_repos", [])
        if target_repos:
            return target_repos

        # 如果没有指定目标仓库，使用repositories中enabled的仓库
        repositories = self.config.get("repositories", [])
        return [
            {"owner": r["owner"], "repo": r["repo"]}
            for r in repositories
            if r.get("enabled", True)
        ]

    def run_interaction_round(self) -> bool:
        """执行单轮互动任务"""
        if not self.enabled:
            self.logger.info("Interaction task is disabled, skipping")
            return True

        if not self.access_token:
            self.logger.warning("GitHub access_token is required for interaction tasks")
            return False

        self.logger.info("=" * 60)
        self.logger.info("Starting interaction round for all target repositories")
        self.logger.info("=" * 60)

        any_success = False
        for repo_info in self.target_repos:
            owner = repo_info.get("owner", "")
            repo = repo_info.get("repo", "")

            if not owner or not repo:
                continue

            try:
                self.logger.info(f"--- Interaction for {owner}/{repo} Start ---")

                # Issue互动
                if "issue" in self.interaction_types or "all" in self.interaction_types:
                    issue_result = self._skill_issue_interaction(owner, repo)
                    if issue_result.get("success"):
                        any_success = True
                        interacted = issue_result.get("interacted", 0)
                        self.stats["issue_interactions"] += interacted
                        self.stats["total_interactions"] += interacted
                        self.logger.info(f"Issue interaction for {owner}/{repo}: {interacted} issues commented")

                # PR互动
                if "pr" in self.interaction_types or "all" in self.interaction_types:
                    pr_result = self._skill_pr_interaction(owner, repo)
                    if pr_result.get("success"):
                        any_success = True
                        interacted = pr_result.get("interacted", 0)
                        self.stats["pr_interactions"] += interacted
                        self.stats["total_interactions"] += interacted
                        self.logger.info(f"PR interaction for {owner}/{repo}: {interacted} PRs commented")

                self.logger.info(f"--- Interaction for {owner}/{repo} End ---")

            except Exception as e:
                self.logger.error(f"Error during interaction for {owner}/{repo}: {e}")
                self.stats["failed_interactions"] += 1

        self.logger.info("=" * 60)
        self.logger.info("Interaction round completed")
        self.logger.info(f"Stats: total={self.stats['total_interactions']}, "
                        f"issues={self.stats['issue_interactions']}, "
                        f"prs={self.stats['pr_interactions']}, "
                        f"failed={self.stats['failed_interactions']}")
        self.logger.info("=" * 60)
        return any_success

    def _skill_issue_interaction(self, owner: str, repo: str) -> Dict:
        """执行Issue互动技能"""
        self.logger.info(f"[Skill] issue_interaction starting for {owner}/{repo}...")
        result = self.skill_dispatcher.run_skill_task(
            "skill_issue_interaction",
            access_token=self.access_token,
            owner=owner,
            repo=repo,
            rules=self.rules_cfg
        )
        return result

    def _skill_pr_interaction(self, owner: str, repo: str) -> Dict:
        """执行PR互动技能"""
        self.logger.info(f"[Skill] pr_interaction starting for {owner}/{repo}...")
        result = self.skill_dispatcher.run_skill_task(
            "skill_pr_interaction",
            access_token=self.access_token,
            owner=owner,
            repo=repo,
            rules=self.rules_cfg
        )
        return result

    def run_continuous(self, stop_event=None):
        """持续运行互动任务（用于后台线程）"""
        self.logger.info(f"Starting continuous interaction runner (interval={self.interval_hours}h)")

        while True:
            try:
                if stop_event and stop_event.is_set():
                    self.logger.info("Interaction stop event received, exiting")
                    break

                self.run_interaction_round()

            except Exception as e:
                self.logger.error(f"Error in interaction cycle: {e}")

            # 等待下一次执行
            sleep_seconds = self.interval_hours * 3600
            self.logger.info(f"Interaction sleeping for {sleep_seconds} seconds...")

            if stop_event:
                stop_event.wait(sleep_seconds)
            else:
                time.sleep(sleep_seconds)


class ApplicationOwnerAgent:
    """
    Application Owner Agent
    系统唯一调度中枢，负责任务拆解、顺序编排、质量卡点、失败判定、流程流转
    支持多仓库轮询 + 互动任务
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger()
        self.rule_loader = RuleLoader()
        self.skill_dispatcher = SkillDispatcher()
        self.wiki_reader = WikiContextReader()
        self.auditor = ChangeAuditor()
        self.blacklist = BlacklistControl()

        # 路径配置
        paths = config.get("paths", {})
        self.local_repo_base = paths.get("local_repo", "local_repo")
        self.temp_backup = paths.get("temp_backup", "temp_backup")
        self.logs_dir = paths.get("logs", "logs")

        # 任务配置
        task_cfg = config.get("task", {})
        self.interval = task_cfg.get("interval", 10)
        self.retry = task_cfg.get("retry", 3)
        self.timeout = task_cfg.get("timeout", 300)

        # 多仓库配置
        self.repositories: List[Dict[str, Any]] = config.get("repositories", [])
        if not self.repositories:
            # 兼容旧配置：单仓库模式
            github_cfg = config.get("github", {})
            single_repo = {
                "owner": github_cfg.get("owner", ""),
                "repo": github_cfg.get("repo", ""),
                "branch": github_cfg.get("branch", "main"),
                "enabled": True
            }
            if single_repo["owner"] and single_repo["repo"]:
                self.repositories = [single_repo]

        # 初始化仓库运行器
        self.repo_runners: List[RepoTaskRunner] = []
        for repo_cfg in self.repositories:
            runner = RepoTaskRunner(
                repo_config=repo_cfg,
                global_config=config,
                skill_dispatcher=self.skill_dispatcher,
                wiki_reader=self.wiki_reader,
                auditor=self.auditor,
                blacklist=self.blacklist
            )
            self.repo_runners.append(runner)

        # 初始化互动任务运行器
        self.interaction_runner = InteractionTaskRunner(config, self.skill_dispatcher)

        # 后台线程
        self._interaction_thread = None
        self._interaction_stop_event = threading.Event()

        # 全局统计
        self.stats = {
            "total_tasks": 0,
            "total_issues": 0,
            "success_repairs": 0,
            "failed_repairs": 0,
            "blacklisted": len(self.blacklist.list_blacklisted()),
            "total_commits": 0,
            "total_interactions": 0
        }

    def initialize(self):
        """初始化加载规则与配置"""
        self.logger.info("=" * 60)
        self.logger.info("Application Owner Agent initializing...")
        self.logger.info("=" * 60)

        # 加载规则
        rules = self.rule_loader.load_all_rules()
        if not rules:
            self.logger.warning("No rules loaded, proceeding with defaults")
        else:
            self.logger.info(f"Loaded {len(rules)} rules")

        # 加载知识库
        self.wiki_reader.load_all()

        # 校验必要配置
        github_cfg = self.config.get("github", {})
        access_token = github_cfg.get("access_token", "")
        if not access_token:
            raise ConfigException("GitHub access_token is required")

        if not self.repo_runners:
            raise ConfigException("No repositories configured")

        for runner in self.repo_runners:
            if not runner.owner or not runner.repo:
                raise ConfigException(f"Repository owner and repo are required for all entries")

        # 确保目录存在
        os.makedirs(self.local_repo_base, exist_ok=True)
        os.makedirs(self.temp_backup, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

        self.logger.info(f"Configured {len(self.repo_runners)} repositories:")
        for runner in self.repo_runners:
            status = "enabled" if runner.enabled else "disabled"
            self.logger.info(f"  - {runner.owner}/{runner.repo} ({runner.branch}) [{status}]")

        # 启动互动后台线程
        if self.interaction_runner.enabled:
            self.logger.info("Starting interaction background thread...")
            self._interaction_stop_event.clear()
            self._interaction_thread = threading.Thread(
                target=self.interaction_runner.run_continuous,
                args=(self._interaction_stop_event,),
                daemon=True,
                name="InteractionThread"
            )
            self._interaction_thread.start()
            self.logger.info("Interaction background thread started")

        self.logger.info("Agent initialization completed")

    def run_single_round(self) -> bool:
        """执行单轮完整任务链路（轮询所有仓库）"""
        self.logger.info("=" * 60)
        self.logger.info("Starting round for all repositories")
        self.logger.info("=" * 60)

        any_success = False
        for runner in self.repo_runners:
            try:
                result = runner.run_single_round()
                if result:
                    any_success = True
                # 累加统计
                self.stats["total_tasks"] += runner.stats["total_tasks"]
                self.stats["total_issues"] += runner.stats["total_issues"]
                self.stats["success_repairs"] += runner.stats["success_repairs"]
                self.stats["failed_repairs"] += runner.stats["failed_repairs"]
                self.stats["total_commits"] += runner.stats["total_commits"]
            except Exception as e:
                self.logger.error(f"Error processing {runner.owner}/{runner.repo}: {e}")

        self.stats["blacklisted"] = len(self.blacklist.list_blacklisted())
        self.stats["total_interactions"] = self.interaction_runner.stats["total_interactions"]
        self.auditor.update_global_summary(self.stats)

        self.logger.info("=" * 60)
        self.logger.info("Round completed for all repositories")
        self.logger.info(f"Stats: tasks={self.stats['total_tasks']}, issues={self.stats['total_issues']}, "
                        f"success={self.stats['success_repairs']}, failed={self.stats['failed_repairs']}, "
                        f"commits={self.stats['total_commits']}, blacklisted={self.stats['blacklisted']}, "
                        f"interactions={self.stats['total_interactions']}")
        self.logger.info("=" * 60)
        return any_success

    def run(self):
        """启动Agent主循环"""
        self.initialize()
        self.logger.info(f"Agent started, interval={self.interval} minutes, repos={len(self.repo_runners)}")

        try:
            while True:
                start_time = time.time()
                try:
                    self.run_single_round()
                except HarnessException as e:
                    self.logger.error(f"Harness exception in round: {e}")
                except Exception as e:
                    self.logger.error(f"Unexpected exception in round: {e}")

                # 计算休眠时间
                elapsed = time.time() - start_time
                sleep_seconds = max(0, self.interval * 60 - elapsed)
                self.logger.info(f"Round finished, sleeping for {sleep_seconds:.0f} seconds...")
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            self.logger.info("Agent stopped by user")
            # 停止互动线程
            if self._interaction_thread and self._interaction_thread.is_alive():
                self.logger.info("Stopping interaction thread...")
                self._interaction_stop_event.set()
                self._interaction_thread.join(timeout=5)
                self.logger.info("Interaction thread stopped")

def main():
    # 初始化日志
    LogManager(log_dir="logs")
    logger = get_logger()

    try:
        config = load_config("config.yaml")
        agent = ApplicationOwnerAgent(config)
        agent.run()
    except ConfigException as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
