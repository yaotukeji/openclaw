import subprocess
import shlex
import os
from typing import List, Optional, Tuple


class CmdExecutor:
    """安全执行系统命令、捕获返回与异常"""

    @staticmethod
    def run(
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        shell: bool = True,
        env: Optional[dict] = None
    ) -> Tuple[int, str, str]:
        """
        执行命令，返回 (returncode, stdout, stderr)
        """
        try:
            merged_env = os.environ.copy()
            if env:
                merged_env.update(env)

            if not shell:
                command = shlex.split(command)

            proc = subprocess.run(
                command,
                cwd=cwd,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
                encoding="utf-8",
                errors="ignore"
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)

    @staticmethod
    def run_safe(
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 300
    ) -> str:
        """
        安全执行，成功返回stdout，失败抛出异常
        """
        returncode, stdout, stderr = CmdExecutor.run(command, cwd=cwd, timeout=timeout)
        if returncode != 0:
            raise RuntimeError(f"Command failed: {command}\nstderr: {stderr}")
        return stdout

    @staticmethod
    def git(
        args: List[str],
        cwd: Optional[str] = None,
        timeout: int = 120
    ) -> Tuple[int, str, str]:
        """
        执行git命令
        """
        command = "git " + " ".join(args)
        return CmdExecutor.run(command, cwd=cwd, timeout=timeout)
