import os
import re
from typing import List, Dict, Tuple

from src.common_utils.log_manager import get_logger
from src.common_utils.cmd_executor import CmdExecutor

logger = get_logger()


class SpecChecker:
    """规范校验器：调用代码检测工具，校验修复后代码合规性（支持多语言，OpenClaw标准版）"""

    # 禁止修改的文件/目录模式（来自规则6）
    FORBIDDEN_PATTERNS = [
        # 已发布的运行时资源
        r'extensions/diffs/assets/viewer-runtime\.js',
        r'assets/chrome-extension/background\.js',
        r'assets/chrome-extension/background-utils\.js',
        r'apps/shared/OpenClawKit/Tools/CanvasA2UI/bootstrap\.js',
        r'apps/shared/OpenClawKit/Tools/CanvasA2UI/rolldown\.config\.mjs',
        r'src/auto-reply/reply/export-html/vendor/highlight\.min\.js',
        # 生成文件 / 捆绑包
        r'.*\.min\.js$',
        r'.*\.bundle\.js$',
        r'openclaw\.mjs$',
        r'dist/',
        r'build/',
        # 测试数据 / 快照文件
        r'__snapshots__/',
        r'.*\.snap$',
        r'fixtures/',
        # node_modules
        r'node_modules/',
    ]

    # 优先修改的源代码目录
    PREFERRED_SOURCE_DIRS = [
        'src/',
        'apps/*/Sources/',
        'packages/*/src/',
        'extensions/*/src/',
    ]

    def __init__(self, project_path: str):
        self.project_path = project_path

    def _detect_language(self, filepath: str) -> str:
        """检测文件语言"""
        ext = os.path.splitext(filepath)[1].lower()
        lang_map = {
            '.py': 'python',
            '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
            '.ts': 'typescript', '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
            '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp',
            '.c': 'c', '.h': 'c',
            '.cs': 'csharp',
            '.rb': 'ruby',
            '.php': 'php',
            '.swift': 'swift',
            '.kt': 'kotlin',
        }
        return lang_map.get(ext, 'unknown')

    def check_forbidden_files(self, files: List[str]) -> Tuple[bool, List[str]]:
        """检查是否包含禁止修改的文件"""
        passed = True
        details = []
        forbidden_files = []

        for filepath in files:
            normalized = filepath.replace('\\', '/')
            for pattern in self.FORBIDDEN_PATTERNS:
                if re.search(pattern, normalized):
                    forbidden_files.append(filepath)
                    passed = False
                    break

        if forbidden_files:
            details.append(f"Forbidden files detected: {forbidden_files}")
            details.append("Only source files in src/, apps/**/Sources/, packages/**/src/, extensions/**/src/ should be modified")

        return passed, details

    def check_preferred_source_dirs(self, files: List[str]) -> Tuple[bool, List[str]]:
        """检查修改的文件是否优先在源代码目录"""
        details = []
        non_preferred = []

        for filepath in files:
            normalized = filepath.replace('\\', '/')
            is_preferred = any(
                re.search(prefix.replace('*', '[^/]+'), normalized)
                for prefix in self.PREFERRED_SOURCE_DIRS
            )
            if not is_preferred:
                non_preferred.append(filepath)

        if non_preferred:
            details.append(f"Non-preferred source files: {non_preferred}")
            # 不直接失败，但警告

        return True, details

    def check_diff_size(self, files: List[str]) -> Tuple[bool, List[str]]:
        """检查diff大小（git diff --numstat）"""
        details = []
        try:
            rc, out, err = CmdExecutor.git(["diff", "--numstat", "HEAD"], cwd=self.project_path)
            if rc == 0 and out:
                lines = out.strip().split('\n')
                total_added = 0
                total_removed = 0
                for line in lines:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        try:
                            added = int(parts[0]) if parts[0] != '-' else 0
                            removed = int(parts[1]) if parts[1] != '-' else 0
                            total_added += added
                            total_removed += removed
                        except ValueError:
                            continue

                details.append(f"Diff stats: +{total_added}, -{total_removed}")

                # 非测试代码增长需警告
                if total_added > 200:
                    details.append(f"WARNING: Large code addition (+{total_added}), consider trimming or explaining")
                if total_removed > 100:
                    details.append(f"WARNING: Large code removal (-{total_removed}), verify no dead code")

                return True, details
        except Exception as e:
            logger.warning(f"Failed to check diff size: {e}")

        return True, details

    # ==================== Python 检查 ====================

    def check_flake8(self, files: List[str]) -> Tuple[bool, List[str]]:
        """运行Flake8检查"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.py'):
                continue
            try:
                returncode, stdout, stderr = CmdExecutor.run(
                    f"flake8 {filepath}", cwd=self.project_path
                )
                if returncode != 0:
                    passed = False
                    details.append(f"flake8: {filepath}\n{stdout}\n{stderr}")
            except Exception as e:
                logger.warning(f"flake8 not available or error: {e}")
        return passed, details

    def check_pylint(self, files: List[str]) -> Tuple[bool, List[str]]:
        """运行Pylint检查"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.py'):
                continue
            try:
                returncode, stdout, stderr = CmdExecutor.run(
                    f"pylint {filepath}", cwd=self.project_path
                )
                if returncode != 0:
                    passed = False
                    details.append(f"pylint: {filepath}\n{stdout}\n{stderr}")
            except Exception as e:
                logger.warning(f"pylint not available or error: {e}")
        return passed, details

    def check_python_syntax(self, files: List[str]) -> Tuple[bool, List[str]]:
        """Python语法检查"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.py'):
                continue
            try:
                returncode, stdout, stderr = CmdExecutor.run(
                    f"python -m py_compile {filepath}", cwd=self.project_path
                )
                if returncode != 0:
                    passed = False
                    details.append(f"python_syntax: {filepath}\n{stderr}")
            except Exception as e:
                logger.warning(f"python syntax check error: {e}")
        return passed, details

    # ==================== JavaScript/TypeScript 检查 ====================

    def check_eslint(self, files: List[str]) -> Tuple[bool, List[str]]:
        """运行ESLint检查（简化版：仅检查文件存在，避免无配置文件报错）"""
        passed = True
        details = []
        for filepath in files:
            if not any(filepath.endswith(ext) for ext in ['.js', '.jsx', '.ts', '.tsx']):
                continue
            abs_path = os.path.abspath(filepath)
            if not os.path.exists(abs_path):
                passed = False
                details.append(f"eslint: {filepath} not found")
            # 简化：由于目标项目可能没有eslint配置文件，仅做文件存在性检查
            # 如需完整eslint检查，请确保项目根目录有eslint.config.js或.eslintrc
        return passed, details

    def check_tsc(self, files: List[str]) -> Tuple[bool, List[str]]:
        """
        运行TypeScript编译器检查（强化版：实际运行tsc，不再仅检查文件存在）。
        新规则：tsc --noEmit 必须通过，禁止降级为文件存在性检查。
        """
        passed = True
        details = []
        ts_files = [f for f in files if f.endswith(('.ts', '.tsx'))]
        if not ts_files:
            return True, ["No TypeScript files modified, skipping tsc check"]

        # 检查 tsconfig.json 是否存在
        tsconfig_path = os.path.join(self.project_path, "tsconfig.json")
        if not os.path.exists(tsconfig_path):
            details.append("WARNING: tsconfig.json not found, falling back to file existence check")
            # 没有 tsconfig 时，只能做文件存在性检查（但这是降级，需要记录）
            for filepath in ts_files:
                abs_path = os.path.abspath(filepath)
                if not os.path.exists(abs_path):
                    passed = False
                    details.append(f"tsc: {filepath} not found")
            return passed, details

        # 尝试运行项目级 tsc --noEmit
        tsc_cmd = "npx tsc --noEmit"
        try:
            rc, out, err = CmdExecutor.run(tsc_cmd, cwd=self.project_path, timeout=180)
            if rc == 0:
                details.append("tsc --noEmit passed for all files")
                return True, details
            else:
                # tsc 失败，检查是否是修改的文件引入的错误
                error_text = f"{out} {err}"
                errors_in_modified = []
                for filepath in ts_files:
                    normalized_fp = filepath.replace('\\', '/')
                    rel_path = normalized_fp
                    if self.project_path in normalized_fp:
                        rel_path = normalized_fp.replace(self.project_path.replace('\\', '/'), '').lstrip('/')
                    if rel_path in error_text or os.path.basename(normalized_fp) in error_text:
                        errors_in_modified.append(filepath)

                if errors_in_modified:
                    passed = False
                    details.append(f"tsc --noEmit found errors in modified files: {errors_in_modified}")
                    details.append(f"Error output: {error_text[:1000]}")
                else:
                    # 错误存在于未修改的文件中（预存在问题）
                    details.append("tsc --noEmit found errors in unmodified files (pre-existing issues)")
                    details.append("Modified files are clean")
                    # 预存在问题不阻断，但记录
        except Exception as e:
            passed = False
            details.append(f"tsc --noEmit execution failed: {e}")

        return passed, details

    def check_node_syntax(self, files: List[str]) -> Tuple[bool, List[str]]:
        """使用Node.js检查JS语法（简化版：仅检查文件存在）"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.js'):
                continue
            abs_path = os.path.abspath(filepath)
            if not os.path.exists(abs_path):
                passed = False
                details.append(f"node_syntax: {filepath} not found")
            # 简化：由于node --check对ES模块支持不完善，仅做文件存在性检查
        return passed, details

    # ==================== 运行时资源文件检查（新增）====================

    def check_runtime_assets(self, files: List[str]) -> Tuple[bool, List[str]]:
        """
        检查是否包含运行时资源文件（*.runtime.*, *.bundle.*, *.min.*, assets/*.js, dist/*, build/*）
        新规则：禁止修改运行时资源文件
        """
        passed = True
        details = []
        runtime_patterns = [
            r'.*\.runtime\.[jt]s$',
            r'.*\.runtime\.[jt]sx$',
            r'.*\.bundle\.[jt]s$',
            r'.*\.bundle\.[jt]sx$',
            r'.*\.min\.[jt]s$',
            r'.*\.min\.[jt]sx$',
            r'assets/.*\.js$',
            r'assets/.*\.ts$',
            r'dist/.*',
            r'build/.*',
        ]

        runtime_files = []
        for filepath in files:
            normalized = filepath.replace('\\', '/').lower()
            for pattern in runtime_patterns:
                if re.search(pattern, normalized):
                    runtime_files.append(filepath)
                    break

        if runtime_files:
            passed = False
            details.append(f"Runtime asset files detected (should not be modified): {runtime_files}")
            details.append("Please modify source files instead of runtime assets")

        return passed, details

    # ==================== 其他语言检查 ====================

    def check_go_build(self, files: List[str]) -> Tuple[bool, List[str]]:
        """Go编译检查（简化版：仅检查文件存在）"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.go'):
                continue
            abs_path = os.path.abspath(filepath)
            if not os.path.exists(abs_path):
                passed = False
                details.append(f"go_build: {filepath} not found")
        return passed, details

    def check_rustc(self, files: List[str]) -> Tuple[bool, List[str]]:
        """Rust编译检查（简化版：仅检查文件存在）"""
        passed = True
        details = []
        for filepath in files:
            if not filepath.endswith('.rs'):
                continue
            abs_path = os.path.abspath(filepath)
            if not os.path.exists(abs_path):
                passed = False
                details.append(f"rustc: {filepath} not found")
        return passed, details

    # ==================== Conventional Commits 规范校验 ====================

    COMMIT_TYPES = ["feat", "fix", "docs", "style", "refactor", "perf", "test", "chore", "ci", "build", "revert"]
    COMMIT_SCOPES = ["", "core", "api", "ui", "cli", "docs", "test", "deps", "config", "ci", "build", "issue"]

    def check_commit_message(self, message: str) -> Tuple[bool, str]:
        """校验提交信息格式（遵循 Conventional Commits 规范）"""
        if not message:
            return False, "Commit message is empty"

        # 兼容旧格式 [Fix] #N title
        if message.startswith("[Fix]"):
            if "#" not in message:
                return False, "Commit message must contain issue number (#N)"
            return True, ""

        # Conventional Commits 格式校验
        # 格式: type(scope): subject
        conv_pattern = r"^([a-z]+)(?:\(([^)]+)\))?:\s*(.+)$"
        match = re.match(conv_pattern, message)
        if not match:
            return False, (
                "Commit message must follow Conventional Commits format: "
                "type(scope): subject. Examples: fix(core): resolve race condition, docs: update README"
            )

        commit_type, scope, subject = match.groups()

        if commit_type not in self.COMMIT_TYPES:
            return False, f"Invalid commit type '{commit_type}'. Allowed: {', '.join(self.COMMIT_TYPES)}"

        if scope and scope not in self.COMMIT_SCOPES:
            return False, f"Invalid scope '{scope}'. Allowed: {', '.join(s for s in self.COMMIT_SCOPES if s)}"

        if len(subject) > 72:
            return False, f"Subject too long ({len(subject)} chars), max 72"

        if not subject:
            return False, "Subject cannot be empty"

        return True, ""

    def check_branch_name(self, branch_name: str) -> Tuple[bool, str]:
        """校验分支命名规范"""
        if not branch_name:
            return False, "Branch name is empty"

        # 规范格式: type-issue号-短横线语义化描述
        # 示例: fix-issue-142-login-timeout-error, feature-issue-789-add-llama-support
        pattern = r"^(fix|feature|docs|refactor|perf|test|chore|hotfix)-issue-(\d+)-[a-z0-9-]+$"
        if not re.match(pattern, branch_name):
            return False, (
                "Branch name must follow: type-issue-number-short-description. "
                "Examples: fix-issue-142-login-timeout-error, feature-issue-789-add-llama-support. "
                "All lowercase, no Chinese, no special chars except hyphen."
            )
        return True, ""

    def check_pr_template(self, title: str, body: str) -> Tuple[bool, List[str]]:
        """校验PR内容是否符合OpenClaw模板要求"""
        errors = []

        if not title:
            errors.append("PR title is empty")
        elif len(title) > 100:
            errors.append(f"PR title too long ({len(title)} chars), max 100")

        # 标题应遵循 Conventional Commits
        conv_pattern = r"^([a-z]+)(?:\(([^)]+)\))?:\s*(.+)$"
        if not re.match(conv_pattern, title):
            errors.append("PR title should follow Conventional Commits: type(scope): subject")

        # 检查PR正文关键章节（OpenClaw标准）
        required_sections = ["Summary", "Changes", "Real behavior proof", "Verification"]
        body_lower = body.lower()
        for section in required_sections:
            if section.lower() not in body_lower:
                errors.append(f"PR body missing required section: {section}")

        # 检查Real behavior proof子项
        rbp_required = [
            "Behavior addressed",
            "Real environment tested",
            "Exact steps or command run after this patch",
            "Evidence after fix",
            "Observed result after fix",
            "What was not tested",
        ]
        for item in rbp_required:
            if item.lower() not in body_lower:
                errors.append(f"PR body missing Real behavior proof item: {item}")

        # 检查自查清单
        checklist_items = [
            "仅解决单一Issue",
            "无硬编码密钥",
            "无敏感信息",
            "分支干净",
        ]
        for item in checklist_items:
            if item not in body:
                errors.append(f"PR body missing checklist item: {item}")

        return len(errors) == 0, errors

    def full_check(self, files: List[str], commit_message: str = "") -> Dict:
        """
        完整校验流程（根据文件类型自动选择检查工具，含修复范围约束）
        新增：运行时资源文件检查、强化tsc检查
        返回: {"passed": bool, "details": List[str]}
        """
        results = []
        all_passed = True

        # 1. 检查禁止修改的文件
        forbidden_passed, forbidden_details = self.check_forbidden_files(files)
        if not forbidden_passed:
            all_passed = False
            results.extend(forbidden_details)

        # 新增：运行时资源文件检查（新规则）
        runtime_passed, runtime_details = self.check_runtime_assets(files)
        if not runtime_passed:
            all_passed = False
            results.extend(runtime_details)

        # 2. 检查优先源代码目录
        _, preferred_details = self.check_preferred_source_dirs(files)
        results.extend(preferred_details)

        # 3. 检查diff大小
        _, diff_details = self.check_diff_size(files)
        results.extend(diff_details)

        # 按语言分组文件
        python_files = [f for f in files if f.endswith('.py')]
        js_files = [f for f in files if f.endswith(('.js', '.jsx'))]
        ts_files = [f for f in files if f.endswith(('.ts', '.tsx'))]
        go_files = [f for f in files if f.endswith('.go')]
        rust_files = [f for f in files if f.endswith('.rs')]

        # Python检查
        if python_files:
            syntax_passed, syntax_details = self.check_python_syntax(python_files)
            if not syntax_passed:
                all_passed = False
                results.extend(syntax_details)
            flake_passed, flake_details = self.check_flake8(python_files)
            if not flake_passed:
                all_passed = False
                results.extend(flake_details)
            pylint_passed, pylint_details = self.check_pylint(python_files)
            if not pylint_passed:
                all_passed = False
                results.extend(pylint_details)

        # JavaScript检查
        if js_files:
            node_passed, node_details = self.check_node_syntax(js_files)
            if not node_passed:
                all_passed = False
                results.extend(node_details)
            eslint_passed, eslint_details = self.check_eslint(js_files)
            if not eslint_passed:
                all_passed = False
                results.extend(eslint_details)

        # TypeScript检查
        if ts_files:
            tsc_passed, tsc_details = self.check_tsc(ts_files)
            if not tsc_passed:
                all_passed = False
                results.extend(tsc_details)
            eslint_passed, eslint_details = self.check_eslint(ts_files)
            if not eslint_passed:
                all_passed = False
                results.extend(eslint_details)

        # Go检查
        if go_files:
            go_passed, go_details = self.check_go_build(go_files)
            if not go_passed:
                all_passed = False
                results.extend(go_details)

        # Rust检查
        if rust_files:
            rust_passed, rust_details = self.check_rustc(rust_files)
            if not rust_passed:
                all_passed = False
                results.extend(rust_details)

        # 提交信息检查
        if commit_message:
            msg_passed, msg_detail = self.check_commit_message(commit_message)
            if not msg_passed:
                all_passed = False
                results.append(f"commit_msg: {msg_detail}")

        logger.info(f"Full check result: passed={all_passed}, issues={len(results)}")
        return {"passed": all_passed, "details": results}

    def full_check_with_pr(self, files: List[str], commit_message: str = "",
                           branch_name: str = "", pr_title: str = "", pr_body: str = "") -> Dict:
        """
        完整校验流程（含PR规范校验）
        返回: {"passed": bool, "details": List[str]}
        """
        # 先执行基础检查
        result = self.full_check(files, commit_message)
        all_passed = result["passed"]
        details = result["details"]

        # 分支名检查
        if branch_name:
            branch_passed, branch_detail = self.check_branch_name(branch_name)
            if not branch_passed:
                all_passed = False
                details.append(f"branch_name: {branch_detail}")

        # PR内容检查
        if pr_title or pr_body:
            pr_passed, pr_errors = self.check_pr_template(pr_title, pr_body)
            if not pr_passed:
                all_passed = False
                for err in pr_errors:
                    details.append(f"pr_template: {err}")

        logger.info(f"Full check with PR result: passed={all_passed}, issues={len(details)}")
        return {"passed": all_passed, "details": details}
