import time
from typing import List, Optional, Dict

from github import Github, GithubException

from src.common_utils.log_manager import get_logger
from src.common_utils.exception_catch import GitHubAPIException

logger = get_logger()


class GitHubConnect:
    """GitHub连接交互：封装密钥校验、仓库实例获取、Issue列表拉取基础API"""

    def __init__(self, access_token: str, owner: str, repo: str, api_limit: int = 50, api_interval: float = 1.0):
        self.access_token = access_token
        self.owner = owner
        self.repo_name = repo
        self.github: Optional[Github] = None
        self.repo = None
        self._api_call_count = 0
        self._api_limit = api_limit
        self._api_interval = api_interval

    def validate_token(self) -> bool:
        """校验GitHub密钥权限"""
        try:
            self.github = Github(self.access_token)
            user = self.github.get_user()
            _ = user.login
            logger.info("GitHub token validated successfully")
            return True
        except GithubException as e:
            logger.error(f"GitHub token validation failed: {e}")
            raise GitHubAPIException(f"Token validation failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during token validation: {e}")
            raise GitHubAPIException(f"Token validation error: {e}")

    def get_repo(self):
        """获取仓库实例"""
        if self.github is None:
            self.validate_token()
        try:
            self.repo = self.github.get_repo(f"{self.owner}/{self.repo_name}")
            logger.info(f"Repository accessed: {self.owner}/{self.repo_name}")
            return self.repo
        except GithubException as e:
            logger.error(f"Failed to access repo {self.owner}/{self.repo_name}: {e}")
            raise GitHubAPIException(f"Repo access failed: {e}")

    def _rate_limited_call(self, func, *args, **kwargs):
        """带限流的API调用，包含403错误重试"""
        if self._api_call_count >= self._api_limit:
            logger.warning(f"API call limit reached for this round ({self._api_limit})")
            raise GitHubAPIException(f"API call limit reached ({self._api_limit})")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                time.sleep(self._api_interval)
                self._api_call_count += 1
                return func(*args, **kwargs)
            except GithubException as e:
                if e.status == 403:
                    # 可能是速率限制或被封禁
                    wait_time = (attempt + 1) * 5  # 递增等待时间
                    logger.warning(f"403 error on attempt {attempt + 1}, waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    if attempt == max_retries - 1:
                        logger.error(f"Failed after {max_retries} retries: {e}")
                        raise
                else:
                    raise
            except Exception as e:
                logger.error(f"Unexpected error in API call: {e}")
                raise

    def fetch_open_issues(self, labels: Optional[List[str]] = None, max_issues: int = 50) -> List[Dict]:
        """拉取Open状态的Issue列表，限制数量避免超时"""
        if self.repo is None:
            self.get_repo()

        try:
            # 先获取所有Open状态的Issue（不传labels，避免AND匹配）
            issues = self._rate_limited_call(
                self.repo.get_issues,
                state="open"
            )
            result = []
            for issue in issues:
                if len(result) >= max_issues:
                    break
                if self._api_call_count >= self._api_limit:
                    break
                issue_labels = [label.name for label in issue.labels]
                # 如果指定了labels，进行OR匹配（任意一个标签匹配即可）
                if labels:
                    if not any(lbl in issue_labels for lbl in labels):
                        continue
                result.append({
                    "number": issue.number,
                    "title": issue.title,
                    "body": issue.body or "",
                    "labels": issue_labels,
                    "state": issue.state,
                    "created_at": issue.created_at.isoformat() if issue.created_at else "",
                    "html_url": issue.html_url
                })
            logger.info(f"Fetched {len(result)} open issues")
            return result
        except GithubException as e:
            logger.error(f"Failed to fetch issues: {e}")
            raise GitHubAPIException(f"Fetch issues failed: {e}")

    def get_issue_linked_prs(self, issue_number: int) -> List[Dict]:
        """获取Issue关联的PR（步骤A）"""
        try:
            issue = self._rate_limited_call(self.repo.get_issue, issue_number)
            # 通过timeline事件获取cross-reference
            timeline = list(self._rate_limited_call(issue.get_timeline))
            linked_prs = []
            for event in timeline:
                if event.event == "cross-referenced" and hasattr(event, 'source'):
                    source = event.source
                    if hasattr(source, 'issue') and source.issue:
                        pr = source.issue
                        if pr.pull_request:
                            linked_prs.append({
                                "number": pr.number,
                                "state": pr.state,
                                "html_url": pr.html_url
                            })
            return linked_prs
        except Exception as e:
            logger.warning(f"Failed to get linked PRs for issue #{issue_number}: {e}")
            return []

    def search_open_prs_by_keyword(self, keyword: str) -> List[Dict]:
        """搜索标题/正文含关键词的open PR（步骤B）"""
        try:
            query = f"repo:{self.owner}/{self.repo_name} is:pr is:open {keyword}"
            prs = self._rate_limited_call(self.github.search_issues, query)
            result = []
            # 安全遍历搜索结果
            try:
                pr_list = list(prs)
            except Exception:
                pr_list = []
            for pr in pr_list[:10]:  # 限制搜索数量
                try:
                    result.append({
                        "number": pr.number,
                        "title": pr.title,
                        "state": pr.state,
                        "html_url": pr.html_url
                    })
                except Exception:
                    continue
            return result
        except Exception as e:
            logger.warning(f"Failed to search PRs by keyword '{keyword}': {e}")
            return []

    def get_issue_comments(self, issue_number: int) -> List[Dict]:
        """获取Issue评论（步骤C：检查是否有人声称要修复）"""
        try:
            issue = self._rate_limited_call(self.repo.get_issue, issue_number)
            comments = list(self._rate_limited_call(issue.get_comments))
            return [{
                "user": c.user.login if c.user else "",
                "body": c.body or "",
                "created_at": c.created_at.isoformat() if c.created_at else ""
            } for c in comments]
        except Exception as e:
            logger.warning(f"Failed to get comments for issue #{issue_number}: {e}")
            return []

    def get_issue_timeline_cross_refs(self, issue_number: int) -> List[str]:
        """获取timeline中的cross-reference URL（步骤D）"""
        try:
            issue = self._rate_limited_call(self.repo.get_issue, issue_number)
            timeline = list(self._rate_limited_call(issue.get_timeline))
            refs = []
            for event in timeline:
                if event.event == "cross-referenced":
                    if hasattr(event, 'source') and event.source:
                        source = event.source
                        if hasattr(source, 'issue') and source.issue:
                            refs.append(source.issue.html_url)
            return refs
        except Exception as e:
            logger.warning(f"Failed to get timeline cross-refs for issue #{issue_number}: {e}")
            return []

    def create_pull_request(self, title: str, body: str, head: str, base: str = "main") -> Dict:
        """
        创建Pull Request
        head格式: "xuwei-xy:fix-branch-name" (fork_owner:branch)
        """
        if self.repo is None:
            self.get_repo()
        try:
            pr = self._rate_limited_call(
                self.repo.create_pull,
                title=title,
                body=body,
                head=head,
                base=base
            )
            logger.info(f"Created PR #{pr.number}: {pr.html_url}")
            return {
                "success": True,
                "number": pr.number,
                "url": pr.html_url,
                "title": pr.title
            }
        except GithubException as e:
            logger.error(f"Failed to create PR: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error creating PR: {e}")
            return {"success": False, "error": str(e)}

    def reset_api_counter(self):
        """重置单轮API计数器"""
        self._api_call_count = 0

    def get_api_call_count(self) -> int:
        return self._api_call_count
