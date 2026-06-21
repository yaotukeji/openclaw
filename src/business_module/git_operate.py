import os
from typing import Optional, Tuple

from src.common_utils.log_manager import get_logger
from src.common_utils.cmd_executor import CmdExecutor
from src.common_utils.exception_catch import GitOperationException

logger = get_logger()


class GitOperate:
    """Git操作封装：克隆、拉取、暂存、提交、推送全套命令调用"""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def clone(self, remote_url: str, target_dir: str) -> bool:
        """克隆远程仓库"""
        try:
            returncode, stdout, stderr = CmdExecutor.run(
                f"git clone {remote_url} {target_dir}", timeout=300
            )
            if returncode == 0:
                logger.info(f"Cloned {remote_url} to {target_dir}")
                return True
            else:
                logger.error(f"Clone failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Clone exception: {e}")
            return False

    def pull(self, branch: str = "main") -> bool:
        """拉取最新代码"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["pull", "origin", branch], cwd=self.repo_path, timeout=120
            )
            if returncode == 0:
                logger.info(f"Pulled latest code for branch {branch}")
                return True
            else:
                logger.error(f"Pull failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Pull exception: {e}")
            return False

    def get_current_commit_hash(self) -> str:
        """获取当前提交哈希"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["rev-parse", "HEAD"], cwd=self.repo_path
            )
            if returncode == 0:
                return stdout.strip()
            return ""
        except Exception:
            return ""

    def is_clean(self) -> bool:
        """检查工作区是否干净"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["status", "--porcelain"], cwd=self.repo_path
            )
            return returncode == 0 and not stdout.strip()
        except Exception:
            return False

    def add(self, files: list = None, force: bool = False) -> bool:
        """添加文件到暂存区"""
        try:
            if files:
                for f in files:
                    # 统一使用正斜杠避免Windows路径问题
                    normalized = f.replace("\\", "/")
                    cmd = ["add"]
                    if force:
                        cmd.append("-f")
                    cmd.append(normalized)
                    returncode, stdout, stderr = CmdExecutor.git(
                        cmd, cwd=self.repo_path
                    )
                    if returncode != 0:
                        logger.error(f"Git add failed for {normalized}: {stderr}")
                        return False
            else:
                cmd = ["add"]
                if force:
                    cmd.append("-f")
                cmd.append(".")
                returncode, stdout, stderr = CmdExecutor.git(
                    cmd, cwd=self.repo_path
                )
                if returncode != 0:
                    logger.error(f"Git add failed: {stderr}")
                    return False
            logger.info("Git add completed")
            return True
        except Exception as e:
            logger.error(f"Git add exception: {e}")
            return False

    def commit(self, message: str) -> Tuple[bool, str]:
        """提交代码（跳过 pre-commit hook，避免仓库特定的 hook 脚本问题）"""
        try:
            # 使用引号包裹提交信息，避免特殊字符（如 #、空格）被shell解析
            safe_message = message.replace('"', '\\"')
            returncode, stdout, stderr = CmdExecutor.run(
                f'git commit --no-verify -m "{safe_message}"',
                cwd=self.repo_path
            )
            if returncode == 0:
                commit_hash = self.get_current_commit_hash()
                logger.info(f"Committed: {message} ({commit_hash})")
                return True, commit_hash
            else:
                logger.error(f"Git commit failed: {stderr}")
                return False, ""
        except Exception as e:
            logger.error(f"Git commit exception: {e}")
            return False, ""

    def push(self, branch: str = "main", remote: str = "origin", force: bool = False) -> bool:
        """推送到远程"""
        try:
            cmd = ["push", remote, branch]
            if force:
                cmd.insert(1, "--force")
            returncode, stdout, stderr = CmdExecutor.git(
                cmd, cwd=self.repo_path, timeout=120
            )
            if returncode == 0:
                logger.info(f"Pushed to {remote}/{branch}" + (" (force)" if force else ""))
                return True
            else:
                logger.error(f"Git push failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Git push exception: {e}")
            return False

    def create_branch(self, branch_name: str, base_branch: str = "main") -> bool:
        """基于指定分支创建新分支"""
        try:
            # 先切换到基础分支并确保干净
            returncode, stdout, stderr = CmdExecutor.git(
                ["checkout", base_branch], cwd=self.repo_path
            )
            if returncode != 0:
                logger.error(f"Git checkout {base_branch} failed: {stderr}")
                return False
            # 重置基础分支到干净状态，避免携带未提交修改
            CmdExecutor.git(["reset", "--hard", "HEAD"], cwd=self.repo_path)
            CmdExecutor.git(["clean", "-fd"], cwd=self.repo_path)
            # 创建并切换到新分支
            returncode, stdout, stderr = CmdExecutor.git(
                ["checkout", "-b", branch_name], cwd=self.repo_path
            )
            if returncode == 0:
                logger.info(f"Created branch: {branch_name} based on {base_branch}")
                return True
            else:
                logger.error(f"Git branch creation failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Git branch exception: {e}")
            return False

    def branch_exists(self, branch_name: str) -> bool:
        """检查分支是否已存在"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["branch", "--list", branch_name], cwd=self.repo_path
            )
            return returncode == 0 and bool(stdout.strip())
        except Exception:
            return False

    def set_remote_url(self, remote_name: str, url: str) -> bool:
        """设置远程仓库URL"""
        try:
            # 先检查remote是否存在
            returncode, stdout, stderr = CmdExecutor.git(
                ["remote", "get-url", remote_name], cwd=self.repo_path
            )
            if returncode == 0:
                # 存在则更新
                returncode, stdout, stderr = CmdExecutor.git(
                    ["remote", "set-url", remote_name, url], cwd=self.repo_path
                )
            else:
                # 不存在则添加
                returncode, stdout, stderr = CmdExecutor.git(
                    ["remote", "add", remote_name, url], cwd=self.repo_path
                )
            if returncode == 0:
                logger.info(f"Set remote {remote_name} to {url}")
                return True
            else:
                logger.error(f"Git remote set failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Git remote exception: {e}")
            return False

    def fetch(self, branch: str = "main") -> bool:
        """获取远程最新代码（不合并）"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["fetch", "origin", branch], cwd=self.repo_path, timeout=120
            )
            if returncode == 0:
                logger.info(f"Fetched origin/{branch}")
                return True
            else:
                logger.error(f"Fetch failed: {stderr}")
                return False
        except Exception as e:
            logger.error(f"Fetch exception: {e}")
            return False

    def get_remote_commit_hash(self, branch: str = "main") -> str:
        """获取远程分支的最新 commit hash"""
        try:
            returncode, stdout, stderr = CmdExecutor.git(
                ["rev-parse", f"origin/{branch}"], cwd=self.repo_path
            )
            if returncode == 0:
                return stdout.strip()
            return ""
        except Exception:
            return ""

    def has_remote_changes(self, branch: str = "main") -> bool:
        """检查远程是否有更新"""
        try:
            CmdExecutor.git(["fetch", "origin", branch], cwd=self.repo_path)
            returncode, stdout, stderr = CmdExecutor.git(
                ["log", "HEAD..origin/" + branch, "--oneline"], cwd=self.repo_path
            )
            return returncode == 0 and bool(stdout.strip())
        except Exception:
            return False
