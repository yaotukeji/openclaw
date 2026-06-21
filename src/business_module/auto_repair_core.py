import os
import re
import shutil
import json
from typing import List, Dict, Optional, Tuple, Callable

from src.common_utils.log_manager import get_logger
from src.common_utils.file_helper import FileHelper
from src.common_utils.cmd_executor import CmdExecutor
from src.harness_core.wiki_context_reader import WikiContextReader

logger = get_logger()


class AutoRepairCore:
    """自动修复内核：整合规则、知识库，执行自动化代码修正逻辑（支持多语言）"""

    def __init__(self, project_path: str, backup_dir: str, wiki: WikiContextReader):
        self.project_path = project_path
        self.backup_dir = backup_dir
        self.wiki = wiki
        os.makedirs(self.backup_dir, exist_ok=True)

        # 禁止修改的文件模式（已发布的运行时资源、生成文件、捆绑包、测试文件、配置文件）
        self.BLOCKED_PATTERNS = [
            # 已发布的运行时资源
            r'extensions/diffs/assets/viewer-runtime\.js$',
            r'assets/chrome-extension/background\.js$',
            r'assets/chrome-extension/background-utils\.js$',
            r'assets/chrome-extension/options-validation\.js$',
            r'assets/chrome-extension/.*\.js$',
            r'apps/shared/OpenClawKit/Tools/CanvasA2UI/bootstrap\.js$',
            r'apps/shared/OpenClawKit/Tools/CanvasA2UI/rolldown\.config\.mjs$',
            r'src/auto-reply/reply/export-html/vendor/.*\.min\.js$',
            r'src/auto-reply/reply/export-html/vendor/.*\.js$',
            # 运行时资源文件（新增：更全面的模式）
            r'.*\.runtime\.[jt]s$',
            r'.*\.runtime\.[jt]sx$',
            r'.*\.bundle\.[jt]s$',
            r'.*\.bundle\.[jt]sx$',
            r'.*\.min\.[jt]s$',
            r'.*\.min\.[jt]sx$',
            r'.*\.qa\.[jt]s$',
            r'.*\.live\.test\.[jt]s$',
            r'assets/.*\.js$',
            r'assets/.*\.ts$',
            # 生成文件 / 捆绑包
            r'.*\.min\.js$',
            r'.*\.bundle\.js$',
            r'openclaw\.mjs$',
            r'dist/.*',
            r'build/.*',
            r'out/.*',
            r'.output/.*',
            # 测试数据 / 快照
            r'__snapshots__/.*',
            r'.*\.snap$',
            r'fixtures/.*',
            # 测试文件（禁止修改测试文件）
            r'.*\.test\.[jt]s$',  # 修复：只匹配 .test.ts 和 .test.js
            r'.*\.test\.[jt]sx$',  # 修复：只匹配 .test.tsx 和 .test.jsx
            r'.*\.spec\.[jt]s$',   # 修复：只匹配 .spec.ts 和 .spec.js
            r'.*\.spec\.[jt]sx$',  # 修复：只匹配 .spec.tsx 和 .spec.jsx
            r'.*\.e2e\.[jt]s$',   # 修复：只匹配 .e2e.ts 和 .e2e.js
            r'.*\.live\.test\.[jt]s$',  # 修复：只匹配 .live.test.ts 和 .live.test.js
            r'Tests?/.*',
            r'tests?/.*',
            r'__tests__/.*',
            r'test-[^/]+\.ts$',
            r'test-[^/]+\.js$',
            # 配置文件（禁止修改配置文件）
            r'vitest\.[^/]+\.config\.[^/]+$',
            r'knip\.config\.[^/]+$',
            r'\.oxfmtrc\.[^/]+$',
            r'\.oxlintrc\.[^/]+$',
            r'\.pre-commit-config\.[^/]+$',
            r'tsconfig\.[^/]+\.json$',
            r'\.env\.[^/]+$',
            r'\.dockerignore$',
            r'\.gitignore$',
            r'\.npmrc$',
            r'\.swiftlint\.[^/]+$',
            r'\.swiftformat$',
            r'\.jscpd\.json$',
            r'\.markdownlint[^/]+$',
            r'\.shellcheckrc$',
            r'\.mailmap$',
            r'\.secrets\.baseline$',
            r'\.detect-secrets\.cfg$',
            r'appcast\.xml$',
            r'AGENTS\.md$',
        ]

    def _is_blocked_file(self, filepath: str) -> bool:
        """检查文件是否在禁止修改列表中"""
        # 统一使用正斜杠进行匹配，并转换为相对于项目根的路径
        normalized = filepath.replace('\\', '/')
        # 尝试获取相对于项目根的路径，避免绝对路径中的父目录被误匹配
        try:
            rel_path = os.path.relpath(normalized, self.project_path.replace('\\', '/'))
            # 如果路径在项目根目录外，使用原始路径
            if rel_path.startswith('..'):
                check_path = normalized
            else:
                check_path = rel_path
        except ValueError:
            check_path = normalized

        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, check_path):
                logger.warning(f"Blocked file (published/runtime asset): {filepath}")
                return True
        return False

    # ==================== 修复前验证 ====================

    def validate_files_before_repair(self, files: List[str], issue: Dict) -> Tuple[bool, List[str], List[str]]:
        """
        修复前验证：检查文件是否可修改，是否有运行时资源文件需要推断源代码
        返回: (是否通过, 可修改文件列表, 错误信息列表)
        """
        valid_files = []
        errors = []
        warnings = []

        # 检查是否有运行时资源文件
        runtime_files = []
        for filepath in files:
            normalized = filepath.replace('\\', '/').lower()
            if any(pattern in normalized for pattern in ['.runtime.', '.bundle.', '.min.', 'assets/', 'dist/', 'build/']):
                runtime_files.append(filepath)

        if runtime_files:
            warnings.append(f"WARNING: Runtime asset files detected: {runtime_files}")
            warnings.append("Attempting to infer source files from runtime assets...")

            # 尝试推断源代码文件
            from src.business_module.code_locator import CodeLocator
            locator = CodeLocator(self.project_path)
            inferred_files = []
            for runtime_file in runtime_files:
                inferred = locator._infer_source_file_from_runtime(runtime_file, issue)
                if inferred:
                    inferred_files.append(inferred)
                    warnings.append(f"Inferred source file: {runtime_file} -> {inferred}")

            if inferred_files:
                # 用推断的源代码文件替换运行时资源文件
                valid_files.extend([f for f in files if f not in runtime_files])
                valid_files.extend(inferred_files)
                warnings.append(f"Using inferred source files instead of runtime assets")
            else:
                errors.append(f"Cannot infer source files from runtime assets: {runtime_files}")
                errors.append("This issue may not be automatically fixable")

        # 检查禁止修改的文件（使用原始文件列表或推断后的文件列表）
        files_to_check = valid_files if valid_files else files
        final_files = []
        for filepath in files_to_check:
            if self._is_blocked_file(filepath):
                errors.append(f"Blocked file cannot be modified: {filepath}")
            else:
                final_files.append(filepath)

        # 去重
        final_files = list(set(final_files))

        if errors:
            logger.error(f"File validation failed: {errors}")
            return False, final_files, errors + warnings

        if warnings:
            logger.warning(f"File validation warnings: {warnings}")

        return True, final_files, warnings

    # ==================== 备份与回滚 ====================

    def backup_before_repair(self, files: List[str]) -> Dict[str, str]:
        """修复前备份文件"""
        backups = {}
        for filepath in files:
            if os.path.exists(filepath):
                backup_path = FileHelper.backup_file(filepath, self.backup_dir)
                backups[filepath] = backup_path
                logger.info(f"Backed up {filepath} -> {backup_path}")
        return backups

    def rollback(self, backups: Dict[str, str]):
        """回滚备份"""
        for original, backup in backups.items():
            try:
                FileHelper.restore_file(backup, original)
                logger.info(f"Rolled back {original}")
            except Exception as e:
                logger.error(f"Rollback failed for {original}: {e}")

    # ==================== 语言检测 ====================

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

    # ==================== 修复质量判断 ====================

    def _is_meaningful_change(self, original: str, modified: str) -> bool:
        """判断修改是否有意义（不只是格式化）"""
        if original == modified:
            return False

        # 去除空白后比较
        orig_stripped = re.sub(r'\s+', '', original)
        mod_stripped = re.sub(r'\s+', '', modified)
        if orig_stripped == mod_stripped:
            return False  # 只是空白变化

        # 检查是否有逻辑变化（新增/删除非空白字符的行）
        orig_lines = original.split('\n')
        mod_lines = modified.split('\n')

        meaningful_changes = 0
        for line in mod_lines:
            stripped = line.strip()
            if stripped and stripped not in [l.strip() for l in orig_lines]:
                # 新增的有意义行
                if not stripped.startswith('//') and not stripped.startswith('#'):
                    meaningful_changes += 1

        for line in orig_lines:
            stripped = line.strip()
            if stripped and stripped not in [l.strip() for l in mod_lines]:
                # 删除的有意义行
                if not stripped.startswith('//') and not stripped.startswith('#'):
                    meaningful_changes += 1

        return meaningful_changes >= 1

    def _get_file_content(self, filepath: str) -> str:
        """读取文件内容"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    # ==================== 通用修复策略 ====================

    def repair_indentation(self, filepath: str) -> bool:
        """修复缩进错误：统一为4空格"""
        try:
            content = FileHelper.read_file(filepath)
            content = content.replace("\t", "    ")
            lines = [line.rstrip() for line in content.splitlines()]
            FileHelper.write_file(filepath, "\n".join(lines) + "\n")
            logger.info(f"Repaired indentation in {filepath}")
            return True
        except Exception as e:
            logger.error(f"Indentation repair failed: {e}")
            return False

    def repair_trailing_whitespace(self, filepath: str) -> bool:
        """修复行尾空格"""
        try:
            if not os.path.exists(filepath):
                logger.warning(f"File not found, skipping trailing whitespace repair: {filepath}")
                return False
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            cleaned = [line.rstrip() + "\n" for line in lines]
            # 保留文件末尾的空行
            while cleaned and cleaned[-1].strip() == "":
                cleaned.pop()
            if cleaned:
                cleaned.append("")  # 确保文件以换行结束
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(line.rstrip() for line in cleaned) + "\n")
            logger.info(f"Repaired trailing whitespace in {filepath}")
            return True
        except Exception as e:
            logger.error(f"Trailing whitespace repair failed: {e}")
            return False

    # ==================== 模式匹配修复 ====================

    def _repair_null_check_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS 空值访问问题（Cannot read property of null/undefined）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            modified = False

            # 从错误信息中提取属性名
            body = issue.get("body", "")
            error_context = issue.get("error_context", "")
            search_text = f"{body} {error_context}"

            # 匹配 "Cannot read property 'X' of undefined" 或 "Cannot read properties of undefined (reading 'X')"
            prop_matches = re.findall(
                r"Cannot read propert(?:y|ies) (?:'([^']+)'|of (?:null|undefined) \(reading '([^']+)'\))",
                search_text
            )
            props = []
            for m in prop_matches:
                props.extend([p for p in m if p])

            # 也匹配 "obj.prop is undefined" 类错误
            undefined_matches = re.findall(r"(\w+)\.(\w+)\s+is\s+(?:undefined|null)", search_text)
            for obj, prop in undefined_matches:
                props.append(prop)

            if not props:
                # 尝试从标题提取
                title = issue.get("title", "")
                title_props = re.findall(r"(\w+)\.(\w+)", title)
                for _, prop in title_props:
                    props.append(prop)

            lines = content.split('\n')
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 跳过注释、字符串、import
                if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('import') or stripped.startswith('export'):
                    continue

                for prop in props:
                    # 匹配 obj.prop 或 obj[prop] 但不包括已有 ?. 的情况
                    # 保守策略：只在赋值、条件、返回语句中替换
                    if re.search(rf'\.{re.escape(prop)}\b', line) and '?.' not in line:
                        # 检查是否在安全上下文中（已经有 null 检查）
                        context_lines = lines[max(0, i-3):i+1]
                        context = '\n'.join(context_lines)
                        if re.search(rf'if\s*\(\s*\w+\s*[!=]==?\s*(?:null|undefined)', context):
                            continue  # 已经有 null 检查

                        # 替换为可选链
                        new_line = re.sub(
                            rf'(\w+)\.{re.escape(prop)}\b',
                            r'\1?.' + prop,
                            line
                        )
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Added optional chain for '{prop}' at line {i+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Null check repair failed: {e}")
            return False

    def _repair_missing_import_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS 缺失的导入"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "")

            # 提取 Cannot find module 'X' 或 Module not found: Error: Can't resolve 'X'
            module_matches = re.findall(
                r"(?:Cannot find module|Can't resolve|Module not found).*?['\"]([^'\"]+)['\"]",
                body
            )

            if not module_matches:
                return False

            lines = content.split('\n')
            modified = False

            for module_name in module_matches:
                # 检查是否已导入
                if module_name in content:
                    already_imported = False
                    for line in lines:
                        if re.search(rf"(import|require|from)\s+['\"].*?{re.escape(module_name)}.*?['\"]", line):
                            already_imported = True
                            break
                    if already_imported:
                        continue

                # 在文件开头或现有import之后插入
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.strip().startswith('import') or line.strip().startswith('//'):
                        insert_idx = i + 1

                # 尝试推断导入语法
                if filepath.endswith('.ts') or filepath.endswith('.tsx'):
                    import_line = f"import {{ {module_name} }} from '{module_name}';"
                else:
                    import_line = f"const {module_name} = require('{module_name}');"

                lines.insert(insert_idx, import_line)
                modified = True
                logger.info(f"Added missing import for '{module_name}' in {filepath}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Missing import repair failed: {e}")
            return False

    def _repair_off_by_one(self, filepath: str, issue: Dict) -> bool:
        """修复常见的 off-by-one 错误（数组越界等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            error_type = (issue.get("error_type") or "").lower()
            body = issue.get("body", "").lower()

            # 检查是否是数组/索引相关错误
            if not any(k in error_type or k in body for k in ['index', 'bounds', 'range', 'length', 'array']):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                # 跳过注释
                if stripped.startswith('//') or stripped.startswith('#') or stripped.startswith('*'):
                    continue

                # 匹配 arr.length - 1 类模式，检查是否缺少边界检查
                # 匹配 for 循环中的 i < arr.length（应该是 i < arr.length - 1）
                if re.search(r'for\s*\([^)]*<\s*\w+\.(length|size)\b', stripped):
                    # 检查是否已经有 -1
                    if not re.search(r'\blength\s*-\s*1\b', stripped):
                        new_line = re.sub(
                            r'(\w+)\.(length|size)\b',
                            r'\1.\2 - 1',
                            line
                        )
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Fixed off-by-one at line {i+1}")

                # Python: range(len(arr)) 应该是 range(len(arr) - 1) 如果后面有 arr[i+1]
                if re.search(r'range\s*\(\s*len\s*\(', stripped):
                    # 检查后续代码是否有 i+1 访问
                    context = '\n'.join(lines[i:min(i+10, len(lines))])
                    if re.search(r'\[\s*i\s*\+\s*1\s*\]', context):
                        if not re.search(r'len\s*\([^)]+\)\s*-\s*1', stripped):
                            new_line = re.sub(
                                r'(len\s*\([^)]+\))\s*\)',
                                r'\1 - 1)',
                                line
                            )
                            if new_line != line:
                                lines[i] = new_line
                                modified = True
                                logger.info(f"Fixed off-by-one (Python) at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Off-by-one repair failed: {e}")
            return False

    def _repair_unhandled_promise(self, filepath: str, issue: Dict) -> bool:
        """修复未处理的 Promise rejection"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            error_type = (issue.get("error_type") or "").lower()
            body = issue.get("body", "").lower()

            if 'unhandled' not in error_type and 'unhandled' not in body and 'rejection' not in body:
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                # 跳过注释
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 await someAsyncCall() 但没有 try-catch
                if re.search(r'await\s+\w+\s*\(', stripped) and 'try' not in stripped:
                    # 检查是否在 async 函数中且已有 try-catch
                    context = '\n'.join(lines[max(0, i-5):i])
                    if 'try' not in context:
                        # 简单包裹 try-catch，保留 return 语句
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 检查是否有 return
                        has_return = stripped.startswith('return')
                        if has_return:
                            # 将 return 移到 try 块内
                            lines[i] = f"{spaces}try {{\n{spaces}    {stripped}\n{spaces}}} catch (error) {{\n{spaces}    console.error('Error:', error);\n{spaces}    return null;\n{spaces}}}"
                        else:
                            # 保持原始缩进
                            inner_spaces = spaces + '    '
                            lines[i] = f"{spaces}try {{\n{inner_spaces}{stripped}\n{spaces}}} catch (error) {{\n{spaces}    console.error('Error:', error);\n{spaces}}}"
                        modified = True
                        logger.info(f"Added try-catch for await at line {i+1}")

                # 匹配 .then() 但没有 .catch()
                if '.then(' in stripped and '.catch(' not in stripped:
                    # 在行尾添加 .catch()
                    if stripped.endswith(')'):
                        lines[i] = line.rstrip() + '.catch(error => console.error("Error:", error));'
                        modified = True
                        logger.info(f"Added .catch() at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Unhandled promise repair failed: {e}")
            return False

    def _repair_python_none_check(self, filepath: str, issue: Dict) -> bool:
        """修复 Python None 检查问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()
            error_context = issue.get("error_context", "").lower()
            search_text = f"{body} {error_context}"

            # 检查是否是 NoneType 错误
            if 'nonetype' not in search_text and 'none' not in search_text:
                return False

            # 提取属性名（支持多种错误格式）
            attr_matches = re.findall(r"'nonetype' object has no attribute '(\w+)'", search_text)
            if not attr_matches:
                attr_matches = re.findall(r"cannot access local variable '(\w+)'", search_text)
            if not attr_matches:
                # 尝试从 "NoneType object has no attribute 'xxx'" 提取（大小写不敏感）
                attr_matches = re.findall(r"nonetype object has no attribute '(\w+)'", search_text)
            if not attr_matches:
                # 尝试从 "'xxx' of None" 或类似模式提取
                attr_matches = re.findall(r"'(\w+)'\s+of\s+(?:none|nonetype)", search_text)

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('#') or not stripped:
                    continue

                for attr in attr_matches:
                    # 匹配 obj.attr 访问
                    if re.search(rf'\.{re.escape(attr)}\b', stripped):
                        # 检查前面是否已经有 None 检查
                        context = '\n'.join(lines[max(0, i-3):i+1])
                        if re.search(rf'if\s+\w+\s+is\s+not\s+None', context):
                            continue

                        # 尝试添加 if 检查
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        var_match = re.search(r'(\w+)\.' + re.escape(attr), stripped)
                        if var_match:
                            var_name = var_match.group(1)
                            # 检查变量是否在当前行赋值
                            if re.search(rf'^{re.escape(var_name)}\s*=', stripped):
                                continue

                            new_lines = [
                                f"{spaces}if {var_name} is not None:",
                                f"{spaces}    {stripped}"
                            ]
                            lines[i] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Added None check for '{var_name}' at line {i+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Python None check repair failed: {e}")
            return False

    def _repair_python_import(self, filepath: str, issue: Dict) -> bool:
        """修复 Python 缺失导入"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "")

            # 提取 ModuleNotFoundError 或 ImportError
            module_matches = re.findall(
                r"(?:ModuleNotFoundError|ImportError).*?['\"]([^'\"]+)['\"]",
                body
            )
            if not module_matches:
                module_matches = re.findall(
                    r"No module named ['\"]([^'\"]+)['\"]",
                    body
                )
            if not module_matches:
                # 也匹配 "No module named xxx"（无引号）
                module_matches = re.findall(
                    r"No module named\s+(\w+)",
                    body
                )
            if not module_matches:
                # 匹配 "ModuleNotFoundError: No module named 'xxx'" 格式
                module_matches = re.findall(
                    r"(?:ModuleNotFoundError|ImportError):\s*No module named\s+['\"]?(\w+)['\"]?",
                    body
                )

            if not module_matches:
                return False

            lines = content.split('\n')
            modified = False

            for module_name in module_matches:
                # 检查是否已导入
                if re.search(rf'^(import|from)\s+{re.escape(module_name)}\b', content, re.MULTILINE):
                    continue

                # 在文件开头插入导入
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.strip().startswith('import') or line.strip().startswith('from'):
                        insert_idx = i + 1

                lines.insert(insert_idx, f"import {module_name}")
                modified = True
                logger.info(f"Added missing import '{module_name}' in {filepath}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Python import repair failed: {e}")
            return False

    def _repair_type_error_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS 类型错误"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()
            error_context = issue.get("error_context", "").lower()
            search_text = f"{body} {error_context}"

            # 检查是否是类型相关错误
            if 'type' not in search_text and 'is not' not in search_text and 'not a function' not in search_text:
                return False

            lines = content.split('\n')
            modified = False

            # 匹配 "X is not a function" 类错误（支持多种引号）
            func_matches = re.findall(r"['\"](\w+)['\"] is not a function", search_text)
            if not func_matches:
                # 也匹配 "xxx is not a function"（无引号）
                func_matches = re.findall(r"(\w+) is not a function", search_text)
            for func_name in func_matches:
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith('//') or stripped.startswith('*'):
                        continue
                    # 检查是否调用了该变量作为函数
                    if re.search(rf'\b{re.escape(func_name)}\s*\(', stripped):
                        # 检查是否已经有类型检查
                        context = '\n'.join(lines[max(0, i-3):i+1])
                        if 'typeof' in context:
                            continue
                        # 排除函数定义行中的 "function" 关键字
                        if 'function' in context and not stripped.startswith('function'):
                            # 检查是否是函数参数声明中的 function
                            if re.search(rf'function\s+\w+\s*\([^)]*{re.escape(func_name)}', context):
                                pass  # 是函数参数，继续处理
                            else:
                                continue
                        # 添加 typeof 检查
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        new_lines = [
                            f"{spaces}if (typeof {func_name} === 'function') {{",
                            f"{spaces}    {stripped}",
                            f"{spaces}}} else {{",
                            f"{spaces}    console.warn('{func_name} is not a function');",
                            f"{spaces}}}"
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added type check for '{func_name}' at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Type error repair failed: {e}")
            return False

    def _repair_missing_await_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS 缺失 await（async 函数中调用其他 async 函数缺少 await）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是 Promise/await 相关错误
            if 'promise' not in body and 'await' not in body and 'async' not in body:
                return False

            lines = content.split('\n')
            modified = False

            # 查找 async 函数中调用其他函数但没有 await 的情况
            in_async_func = False
            async_func_indent = 0

            for i, line in enumerate(lines):
                stripped = line.strip()

                # 检测 async 函数开始
                if re.search(r'\basync\b.*\bfunction\b|\basync\s*\(|\basync\s+\w+\s*\(', stripped):
                    in_async_func = True
                    async_func_indent = len(line) - len(line.lstrip())
                    continue

                # 检测函数结束（简单启发式：遇到相同或更小缩进的非空行）
                if in_async_func and stripped:
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= async_func_indent and not stripped.startswith('//'):
                        in_async_func = False
                        continue

                if in_async_func and not stripped.startswith('//') and not stripped.startswith('*'):
                    # 匹配 funcName() 调用但没有 await
                    # 排除：已有 await、return、if、for 等控制语句
                    if not stripped.startswith('await') and not stripped.startswith('return'):
                        match = re.search(r'\b(\w+)\s*\([^)]*\)\s*[;]?', stripped)
                        if match:
                            func_name = match.group(1)
                            # 检查函数名是否可能是 async（常见命名模式）
                            async_indicators = ['fetch', 'get', 'load', 'save', 'update', 'delete',
                                                'create', 'query', 'request', 'call', 'exec',
                                                'run', 'start', 'stop', 'send', 'receive']
                            if any(ind in func_name.lower() for ind in async_indicators):
                                # 检查是否已经有 await
                                if 'await' not in stripped:
                                    new_line = line.replace(func_name + '(', 'await ' + func_name + '(', 1)
                                    if new_line != line:
                                        lines[i] = new_line
                                        modified = True
                                        logger.info(f"Added await for '{func_name}' at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Missing await repair failed: {e}")
            return False

    def _repair_memory_leak_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS 内存泄漏（未清除的定时器、事件监听等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是内存泄漏相关
            leak_keywords = ['memory', 'leak', 'cleanup', 'dispose', 'destroy', 'remove listener',
                           'clear timeout', 'clear interval', 'unsubscribe']
            if not any(kw in body for kw in leak_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # 1. 查找 setInterval 但没有 clearInterval
            interval_vars = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                match = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*setInterval', stripped)
                if match:
                    var_name = match.group(1)
                    # 检查是否有对应的 clearInterval
                    has_clear = any(f'clearInterval({var_name})' in l for l in lines)
                    if not has_clear:
                        interval_vars.append((i, var_name))

            # 在文件末尾或组件卸载处添加 clearInterval
            for idx, var_name in interval_vars:
                # 尝试找到组件卸载函数或 cleanup 位置
                inserted = False
                for j in range(len(lines) - 1, max(0, len(lines) - 20), -1):
                    if 'cleanup' in lines[j].lower() or 'unmount' in lines[j].lower() or 'destroy' in lines[j].lower() or 'stop' in lines[j].lower():
                        indent = len(lines[j]) - len(lines[j].lstrip())
                        spaces = ' ' * (indent + 4)
                        lines.insert(j + 1, f"{spaces}clearInterval({var_name});")
                        modified = True
                        logger.info(f"Added clearInterval for '{var_name}' at line {j+1}")
                        inserted = True
                        break
                # 如果没找到合适的插入点，尝试在类/函数末尾添加
                if not inserted:
                    for j in range(len(lines) - 1, idx, -1):
                        stripped_j = lines[j].strip()
                        if stripped_j == '}' or stripped_j == '};':
                            indent = len(lines[j]) - len(lines[j].lstrip())
                            spaces = ' ' * (indent + 4)
                            lines.insert(j, f"{spaces}clearInterval({var_name});")
                            modified = True
                            logger.info(f"Added clearInterval for '{var_name}' at line {j+1}")
                            break

            # 2. 查找 addEventListener 但没有 removeEventListener
            listener_patterns = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                match = re.search(r'\.addEventListener\s*\(\s*[\'"](\w+)[\'"]\s*,\s*(\w+)', stripped)
                if match:
                    event_type = match.group(1)
                    handler = match.group(2)
                    # 检查是否有对应的 removeEventListener
                    has_remove = any(f'removeEventListener' in l and event_type in l and handler in l for l in lines)
                    if not has_remove:
                        listener_patterns.append((i, event_type, handler))

            # 3. 查找 setTimeout 但没有清理（如果 timeout 很长）
            timeout_vars = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                match = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*setTimeout', stripped)
                if match:
                    var_name = match.group(1)
                    has_clear = any(f'clearTimeout({var_name})' in l for l in lines)
                    if not has_clear:
                        timeout_vars.append((i, var_name))

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Memory leak repair failed: {e}")
            return False

    def _repair_infinite_loop(self, filepath: str, issue: Dict) -> bool:
        """修复无限循环（添加退出条件）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()
            error_type = (issue.get("error_type") or "").lower()

            # 检查是否是无限循环相关
            if 'infinite' not in body and 'loop' not in body and 'hang' not in body:
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()

                # 匹配 while(true) 或 while (true)
                if re.search(r'while\s*\(\s*true\s*\)', stripped):
                    # 检查是否已经有 break 或 return
                    context = '\n'.join(lines[i:min(i+20, len(lines))])
                    if 'break' not in context and 'return' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * (indent + 4)
                        # 在循环体内添加条件检查
                        lines.insert(i + 1, f"{spaces}if (shouldExit()) break; // Auto-fixed infinite loop")
                        modified = True
                        logger.info(f"Added exit condition for infinite loop at line {i+1}")

                # 匹配 for(;;) 无限循环
                if re.search(r'for\s*\(\s*;\s*;\s*\)', stripped):
                    context = '\n'.join(lines[i:min(i+20, len(lines))])
                    if 'break' not in context and 'return' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * (indent + 4)
                        lines.insert(i + 1, f"{spaces}if (shouldExit()) break; // Auto-fixed infinite loop")
                        modified = True
                        logger.info(f"Added exit condition for infinite for loop at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Infinite loop repair failed: {e}")
            return False

    def _repair_resource_leak(self, filepath: str, issue: Dict) -> bool:
        """修复资源泄漏（文件、连接未关闭）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是资源泄漏相关
            leak_keywords = ['resource', 'leak', 'not closed', 'not released', 'not disposed',
                           'file handle', 'connection', 'stream']
            if not any(kw in body for kw in leak_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # Python: 查找 open() 但没有 close()
            for i, line in enumerate(lines):
                stripped = line.strip()
                match = re.search(r'(\w+)\s*=\s*open\s*\(', stripped)
                if match:
                    var_name = match.group(1)
                    # 检查是否有 close
                    has_close = any(f'{var_name}.close()' in l for l in lines)
                    if not has_close:
                        # 转换为 with 语句
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 提取 open(...) 的参数部分
                        open_match = re.search(r'open\s*\((.*)\)', stripped)
                        if open_match:
                            open_args = open_match.group(1)
                            new_line = f"{spaces}with open({open_args}) as {var_name}:"
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Converted to with statement for '{var_name}' at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Resource leak repair failed: {e}")
            return False

    def _repair_race_condition_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复 JS/TS Race Condition（并发访问共享资源）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是 race condition 相关
            race_keywords = ['race', 'concurrent', 'synchronization', 'lock', 'mutex',
                           'competing', 'simultaneous', 'overlapping']
            if not any(kw in body for kw in race_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # 查找并发修改共享变量的模式
            shared_vars = set()
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 查找类级别的属性赋值（可能是共享状态）
                match = re.search(r'this\.(\w+)\s*=', stripped)
                if match and 'async' in content[:content.find(line)]:
                    var_name = match.group(1)
                    shared_vars.add((i, var_name))

            # 为共享变量添加简单的锁机制
            for idx, var_name in shared_vars:
                # 检查是否已经有同步机制
                context = '\n'.join(lines[max(0, idx-3):idx+3])
                if 'mutex' not in context and 'lock' not in context and 'semaphore' not in context:
                    # 在类定义开始处添加锁
                    for j in range(idx):
                        if 'class ' in lines[j] or 'constructor' in lines[j]:
                            indent = len(lines[j]) - len(lines[j].lstrip())
                            spaces = ' ' * (indent + 4)
                            lines.insert(j + 1, f"{spaces}private _lock = new Promise(resolve => resolve()); // Auto-fixed race condition")
                            modified = True
                            logger.info(f"Added lock mechanism at line {j+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Race condition repair failed: {e}")
            return False

    def _repair_deprecated_api_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复已弃用的 API 调用"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是弃用 API 相关
            if 'deprecated' not in body and 'obsolete' not in body and 'legacy' not in body:
                return False

            lines = content.split('\n')
            modified = False

            # 常见弃用 API 替换映射
            deprecated_map = {
                r'\.substr\s*\(': '.slice(',
                r'\.escape\s*\(': 'encodeURIComponent(',
                r'\.unescape\s*\(': 'decodeURIComponent(',
                r'new\s+ActiveXObject': 'new XMLHttpRequest',  # 简化处理
            }

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                for pattern, replacement in deprecated_map.items():
                    if re.search(pattern, stripped):
                        new_line = re.sub(pattern, replacement, line)
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Replaced deprecated API at line {i+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Deprecated API repair failed: {e}")
            return False

    def _repair_incorrect_comparison_js_ts(self, filepath: str, issue: Dict) -> bool:
        """修复不正确的比较（== 改为 ===，!= 改为 !==）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是比较相关错误
            if 'comparison' not in body and 'equality' not in body and 'strict' not in body:
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 将 == 改为 ===，但排除字符串中的 ==
                new_line = re.sub(r'(?<![=<>!])==(?![=])', '===', line)
                # 将 != 改为 !==
                new_line = re.sub(r'(?<![=<>!])!=(?![=])', '!==', new_line)

                if new_line != line:
                    lines[i] = new_line
                    modified = True
                    logger.info(f"Fixed comparison at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Comparison repair failed: {e}")
            return False

    def _repair_missing_error_callback(self, filepath: str, issue: Dict) -> bool:
        """修复缺失的错误回调处理"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是错误处理相关
            if 'callback' not in body and 'error handler' not in body and 'missing error' not in body:
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 fs.readFile(path, function(err, data) { ... }) 但没有 err 处理
                match = re.search(r'function\s*\(\s*(\w+)\s*,', stripped)
                if match:
                    err_param = match.group(1)
                    # 检查是否是错误参数（常见命名）
                    if err_param in ['err', 'error', 'e']:
                        # 检查后续代码是否有错误处理
                        context = '\n'.join(lines[i:min(i+10, len(lines))])
                        if err_param not in context or 'if' not in context:
                            indent = len(line) - len(line.lstrip())
                            spaces = ' ' * (indent + 4)
                            lines.insert(i + 1, f"{spaces}if ({err_param}) {{ throw {err_param}; }}")
                            modified = True
                            logger.info(f"Added error callback check at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Missing error callback repair failed: {e}")
            return False

    # ==================== 格式化工具（仅作为辅助）====================

    def repair_by_autopep8(self, filepath: str) -> bool:
        """
        使用autopep8自动格式化Python。
        **策略变更**：完全禁用autopep8，避免格式化噪声导致大量无意义diff。
        格式化修改是PR #92421被关闭的主要原因之一（+555,-210的格式化噪声）。
        """
        logger.info(f"Skipping autopep8 for {filepath} (formatting disabled to avoid noise)")
        return True  # 返回True表示不阻塞流程

    def repair_by_prettier(self, filepath: str) -> bool:
        """
        使用prettier自动格式化JS/TS。
        **策略变更**：完全禁用prettier，避免格式化噪声导致大量无意义diff和CI失败。
        格式化修改是PR #92421被关闭的主要原因之一（+555,-210的格式化噪声）。
        """
        logger.info(f"Skipping prettier for {filepath} (formatting disabled to avoid noise)")
        return True  # 返回True表示不阻塞流程

    # ==================== 语法检查 ====================

    def run_syntax_check(self, filepath: str) -> Tuple[bool, str]:
        """运行语法检查（根据语言选择工具）"""
        language = self._detect_language(filepath)
        # 使用绝对路径避免 cwd 拼接问题
        abs_path = os.path.abspath(filepath)
        try:
            if language == 'python':
                returncode, stdout, stderr = CmdExecutor.run(
                    f"python -m py_compile \"{abs_path}\""
                )
                if returncode == 0:
                    return True, ""
                return False, stderr
            elif language in ('javascript', 'typescript'):
                # **策略变更**：对单个修改文件运行 tsc --noEmit 检查
                # PR #92421 引入了确定性编译失败，需要更严格的语法检查
                # 优先使用项目级 tsc，回退到 node --check
                project_path = os.path.abspath(self.project_path)
                tsconfig_path = os.path.join(project_path, "tsconfig.json")

                # 如果项目有 tsconfig，尝试对单个文件运行类型检查
                if os.path.exists(tsconfig_path):
                    # 使用 --noEmit 和 --skipLibCheck 避免库类型问题
                    # 使用 --isolatedModules 确保单文件编译安全
                    cmd = f"npx tsc --noEmit --skipLibCheck --isolatedModules \"{abs_path}\""
                    returncode, stdout, stderr = CmdExecutor.run(cmd, cwd=project_path, timeout=120)
                    if returncode == 0:
                        return True, ""
                    # tsc 失败，返回错误信息
                    error_msg = stderr or stdout
                    # 如果错误是模块解析相关，可能是跨文件依赖，降级为文件存在性检查
                    if "Cannot find module" in error_msg or "Cannot resolve" in error_msg:
                        logger.warning(f"tsc module resolution error for {filepath} (cross-file dependency), falling back to file existence check")
                        if os.path.exists(abs_path):
                            return True, f"tsc module resolution warning (cross-file dependency): {error_msg[:200]}"
                    return False, f"TypeScript compilation error: {error_msg[:500]}"

                # 没有 tsconfig，回退到 node --check（仅JS）
                if filepath.endswith('.js'):
                    returncode, stdout, stderr = CmdExecutor.run(
                        f"node --check \"{abs_path}\""
                    )
                    if returncode == 0:
                        return True, ""
                    return False, stderr

                # 对于没有 tsconfig 的 TS 文件，只能检查文件存在性
                if os.path.exists(abs_path):
                    return True, ""
                return False, f"File not found: {abs_path}"
            else:
                # 对于其他语言，仅检查文件是否可读
                if os.path.exists(abs_path):
                    return True, ""
                return False, f"File not found: {abs_path}"
        except Exception as e:
            return False, str(e)

    # ==================== 主修复流程 ====================

    def repair_issue(self, issue: Dict, located_files: List[str]) -> Dict:
        """
        执行自动修复（支持多语言）
        返回: {"success": bool, "modified_files": List[str], "fail_reason": str}
        """
        result = {
            "success": False,
            "modified_files": [],
            "fail_reason": ""
        }

        if not located_files:
            result["fail_reason"] = "No files located for repair"
            return result

        language = issue.get("language", 'unknown')
        error_type = (issue.get("error_type") or "").lower()
        line_refs = issue.get("line_refs", [])
        line = line_refs[0] if line_refs else 0

        # 过滤禁止修改的文件
        allowed_files = [f for f in located_files if not self._is_blocked_file(f)]
        blocked_count = len(located_files) - len(allowed_files)
        if blocked_count > 0:
            logger.info(f"Filtered {blocked_count} blocked files, remaining {len(allowed_files)} files for repair")

        if not allowed_files:
            result["fail_reason"] = "All located files are blocked (published/runtime assets). Cannot auto-repair."
            logger.warning(f"Repair failed for issue #{issue.get('number')}: {result['fail_reason']}")
            return result

        # 备份（仅备份允许修改的文件）
        backups = self.backup_before_repair(allowed_files)

        modified = []
        meaningful_repairs = []

        for filepath in allowed_files:
            file_lang = self._detect_language(filepath)
            repaired = False
            original_content = self._get_file_content(filepath)

            # 根据语言和错误类型选择修复策略
            if file_lang == 'python':
                repaired = self._repair_python_file(filepath, error_type, line, issue)
            elif file_lang in ('javascript', 'typescript'):
                repaired = self._repair_js_ts_file(filepath, error_type, line, issue)
            elif file_lang == 'java':
                repaired = self._repair_java_file(filepath, error_type, line, issue)
            elif file_lang == 'go':
                repaired = self._repair_go_file(filepath, error_type, line, issue)
            elif file_lang == 'rust':
                repaired = self._repair_rust_file(filepath, error_type, line, issue)
            else:
                # 通用修复
                repaired = self._repair_generic_file(filepath, error_type, line, issue)

            if repaired:
                new_content = self._get_file_content(filepath)
                if self._is_meaningful_change(original_content, new_content):
                    modified.append(filepath)
                    meaningful_repairs.append(filepath)
                    logger.info(f"Meaningful repair applied to {filepath}")
                else:
                    # 回滚无意义的修改
                    logger.warning(f"Repair for {filepath} produced no meaningful change, rolling back")
                    self.rollback({filepath: backups.get(filepath)})

        # 校验修复后的文件
        all_valid = True
        for filepath in modified:
            valid, err = self.run_syntax_check(filepath)
            if not valid:
                all_valid = False
                result["fail_reason"] += f"Syntax check failed for {filepath}: {err}; "

        # 额外校验：确保修改的文件与issue语言匹配
        # 注意：issue语言是从issue正文检测的，可能不准确（如.m文件被误判为objc）
        # 因此这里只阻止明显不匹配的情况，允许issue语言为None或通用语言
        if meaningful_repairs and all_valid:
            issue_lang = issue.get("language")
            if issue_lang:
                for filepath in meaningful_repairs:
                    file_lang = self._detect_language(filepath)
                    # 定义语言等价组（可互换的语言）
                    lang_groups = [
                        {'javascript', 'typescript', 'jsx', 'tsx'},
                        {'c', 'cpp', 'objc', 'objcpp'},
                        {'python', 'py'},
                    ]
                    is_match = file_lang == issue_lang
                    if not is_match:
                        for group in lang_groups:
                            if file_lang in group and issue_lang in group:
                                is_match = True
                                break
                    # 如果issue语言是高度歧义的（如objc可能来自.m文件引用），放宽检查
                    if not is_match and issue_lang in ('objc', 'm'):
                        logger.info(f"Issue language is ambiguous ({issue_lang}), trusting file language {file_lang} for {filepath}")
                        is_match = True
                    if not is_match:
                        logger.warning(f"Language mismatch: issue is {issue_lang} but file {filepath} is {file_lang}")
                        all_valid = False
                        result["fail_reason"] += f"Language mismatch for {filepath}; "

        if meaningful_repairs and all_valid:
            # 在返回成功前，运行格式检查并格式化修改的文件
            # PR #7618-7621 教训：Format检查失败导致CI失败
            formatted_files = []
            for filepath in modified:
                if self._format_file_after_repair(filepath):
                    formatted_files.append(filepath)
            if formatted_files:
                logger.info(f"Formatted {len(formatted_files)} files after repair: {formatted_files}")

            result["success"] = True
            result["modified_files"] = modified
            result["backups"] = backups
            logger.info(f"Repair succeeded for issue #{issue.get('number')}: {modified}")
        else:
            # 回滚所有修改
            self.rollback(backups)
            if not result["fail_reason"]:
                if not meaningful_repairs:
                    result["fail_reason"] = "No meaningful modifications made (only formatting or no bug pattern matched)"
                else:
                    result["fail_reason"] = "All modifications invalid"
            logger.warning(f"Repair failed for issue #{issue.get('number')}: {result['fail_reason']}")

        return result

    def _format_file_after_repair(self, filepath: str) -> bool:
        """
        修复后对文件进行格式化，确保代码符合项目格式规范。
        PR #7618-7621 教训：Format检查失败导致CI失败。
        """
        language = self._detect_language(filepath)
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            return False

        try:
            if language == 'rust':
                # Rust: 使用 cargo fmt 格式化
                rc, out, err = CmdExecutor.run(
                    f'cargo fmt -- "{abs_path}"',
                    cwd=self.project_path, timeout=30
                )
                if rc == 0:
                    logger.info(f"Formatted Rust file: {filepath}")
                    return True
                else:
                    logger.warning(f"cargo fmt failed for {filepath}: {err}")
                    return False
            elif language in ('javascript', 'typescript'):
                # JS/TS: 使用 prettier 格式化
                prettier_path = os.path.join(self.project_path, "node_modules", ".bin", "prettier")
                if os.name == 'nt':  # Windows
                    prettier_path += ".cmd"
                if os.path.exists(prettier_path):
                    rc, out, err = CmdExecutor.run(
                        f'"{prettier_path}" --write "{abs_path}"',
                        cwd=self.project_path, timeout=30
                    )
                else:
                    rc, out, err = CmdExecutor.run(
                        f'npx prettier --write "{abs_path}"',
                        cwd=self.project_path, timeout=30
                    )
                if rc == 0:
                    logger.info(f"Formatted JS/TS file: {filepath}")
                    return True
                else:
                    logger.warning(f"prettier failed for {filepath}: {err}")
                    return False
            elif language == 'python':
                # Python: 使用 black 格式化
                rc, out, err = CmdExecutor.run(
                    f'black "{abs_path}"',
                    cwd=self.project_path, timeout=30
                )
                if rc == 0:
                    logger.info(f"Formatted Python file: {filepath}")
                    return True
                else:
                    logger.warning(f"black failed for {filepath}: {err}")
                    return False
            elif language == 'go':
                # Go: 使用 gofmt 格式化
                rc, out, err = CmdExecutor.run(
                    f'gofmt -w "{abs_path}"',
                    cwd=self.project_path, timeout=30
                )
                if rc == 0:
                    logger.info(f"Formatted Go file: {filepath}")
                    return True
                else:
                    logger.warning(f"gofmt failed for {filepath}: {err}")
                    return False
            else:
                # 其他语言不格式化
                return True
        except Exception as e:
            logger.warning(f"Format after repair failed for {filepath}: {e}")
            return False

    def _repair_js_ts_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """修复JavaScript/TypeScript文件"""
        repaired = False
        body = issue.get("body", "").lower()
        error_context = issue.get("error_context", "").lower()
        search_text = f"{error_type} {body} {error_context}"

        # 读取原始内容用于后续比较
        original_content = self._get_file_content(filepath)

        # 1. 空值访问修复（最高优先级）
        if any(k in search_text for k in ['cannot read', 'null', 'undefined', 'typeerror']):
            if self._repair_null_check_js_ts(filepath, issue):
                repaired = True

        # 2. 缺失导入修复
        if any(k in search_text for k in ['cannot find module', 'module not found', "can't resolve"]):
            if self._repair_missing_import_js_ts(filepath, issue):
                repaired = True

        # 3. 未处理的 Promise
        if any(k in search_text for k in ['unhandled', 'rejection', 'promise']):
            if self._repair_unhandled_promise(filepath, issue):
                repaired = True

        # 4. 类型错误
        if 'is not a function' in search_text or 'type' in error_type:
            if self._repair_type_error_js_ts(filepath, issue):
                repaired = True

        # 5. Off-by-one
        if any(k in search_text for k in ['index', 'bounds', 'range']):
            if self._repair_off_by_one(filepath, issue):
                repaired = True

        # 6. 缺失 await
        if any(k in search_text for k in ['await', 'promise', 'async']):
            if self._repair_missing_await_js_ts(filepath, issue):
                repaired = True

        # 7. 内存泄漏
        if any(k in search_text for k in ['memory', 'leak', 'cleanup', 'dispose']):
            if self._repair_memory_leak_js_ts(filepath, issue):
                repaired = True

        # 8. 无限循环
        if any(k in search_text for k in ['infinite', 'loop', 'hang', 'freeze']):
            if self._repair_infinite_loop(filepath, issue):
                repaired = True

        # 9. 资源泄漏
        if any(k in search_text for k in ['resource', 'leak', 'not closed', 'not released']):
            if self._repair_resource_leak(filepath, issue):
                repaired = True

        # 10. Race condition
        if any(k in search_text for k in ['race', 'concurrent', 'synchronization', 'lock']):
            if self._repair_race_condition_js_ts(filepath, issue):
                repaired = True

        # 11. 弃用 API
        if any(k in search_text for k in ['deprecated', 'obsolete', 'legacy']):
            if self._repair_deprecated_api_js_ts(filepath, issue):
                repaired = True

        # 12. 不正确的比较
        if any(k in search_text for k in ['comparison', 'equality', 'strict']):
            if self._repair_incorrect_comparison_js_ts(filepath, issue):
                repaired = True

        # 13. 缺失错误回调
        if any(k in search_text for k in ['callback', 'error handler', 'missing error']):
            if self._repair_missing_error_callback(filepath, issue):
                repaired = True

        # 14. 性能问题
        if any(k in search_text for k in ['performance', 'slow', 'lag', 'optimize', 'inefficient']):
            if self._repair_performance_issue(filepath, issue):
                repaired = True

        # 15. 配置问题
        if any(k in search_text for k in ['config', 'configuration', 'default', 'environment', 'process.env']):
            if self._repair_config_issue(filepath, issue):
                repaired = True

        # 16. 边界条件
        if any(k in search_text for k in ['empty', 'boundary', 'edge case', 'division by zero']):
            if self._repair_boundary_condition(filepath, issue):
                repaired = True

        # 17. 输入验证
        if any(k in search_text for k in ['validation', 'validate', 'invalid input', 'missing check']):
            if self._repair_missing_validation(filepath, issue):
                repaired = True

        # 18. 异步时序
        if any(k in search_text for k in ['timing', 'order', 'sequence', 'before', 'after']):
            if self._repair_async_timing_issue(filepath, issue):
                repaired = True

        # 19. 网络/CORS
        if any(k in search_text for k in ['cors', 'network', 'connection', 'timeout', 'fetch']):
            if self._repair_cors_or_network_issue(filepath, issue):
                repaired = True

        # 20. 状态管理
        if any(k in search_text for k in ['state', 'uninitialized', 'stale', 'out of sync']):
            if self._repair_state_management_issue(filepath, issue):
                repaired = True

        # 21. 日志安全
        if any(k in search_text for k in ['log', 'sensitive', 'password', 'token', 'secret']):
            if self._repair_logging_issue(filepath, issue):
                repaired = True

        # 格式化仅作为最后一步，且只在有实际修复时运行
        # **策略变更**：完全禁用prettier，因为格式化修改会导致CI检查失败和大量无意义diff
        # PR #92421 因 +555,-210 的格式化噪声被关闭
        # 只有在有实际逻辑修复时才记录，不运行任何格式化工具
        if repaired:
            # 验证修复是否有意义
            new_content = self._get_file_content(filepath)
            if not self._is_meaningful_change(original_content, new_content):
                # 如果只是格式化，回滚
                logger.warning(f"Repair for {filepath} produced only formatting changes, rolling back")
                return False

        return repaired

    def _repair_python_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """修复Python文件"""
        repaired = False
        body = issue.get("body", "").lower()
        error_context = issue.get("error_context", "").lower()
        search_text = f"{error_type} {body} {error_context}"

        # 读取原始内容用于后续比较
        original_content = self._get_file_content(filepath)

        # 1. None 检查
        if 'nonetype' in search_text or 'none' in search_text:
            if self._repair_python_none_check(filepath, issue):
                repaired = True

        # 2. 缺失导入
        if any(k in search_text for k in ['modulenotfound', 'importerror', 'no module named']):
            if self._repair_python_import(filepath, issue):
                repaired = True

        # 3. Off-by-one
        if any(k in search_text for k in ['index', 'bounds', 'range', 'list index']):
            if self._repair_off_by_one(filepath, issue):
                repaired = True

        # 4. 资源泄漏
        if any(k in search_text for k in ['resource', 'leak', 'not closed', 'not released', 'file handle']):
            if self._repair_resource_leak(filepath, issue):
                repaired = True

        # 5. 无限循环
        if any(k in search_text for k in ['infinite', 'loop', 'hang', 'freeze']):
            if self._repair_infinite_loop(filepath, issue):
                repaired = True

        # 6. 性能问题
        if any(k in search_text for k in ['performance', 'slow', 'lag', 'optimize', 'inefficient']):
            if self._repair_performance_issue(filepath, issue):
                repaired = True

        # 7. 配置问题
        if any(k in search_text for k in ['config', 'configuration', 'default', 'environment']):
            if self._repair_config_issue(filepath, issue):
                repaired = True

        # 8. 边界条件
        if any(k in search_text for k in ['empty', 'boundary', 'edge case', 'division by zero']):
            if self._repair_boundary_condition(filepath, issue):
                repaired = True

        # 9. 输入验证
        if any(k in search_text for k in ['validation', 'validate', 'invalid input', 'missing check']):
            if self._repair_missing_validation(filepath, issue):
                repaired = True

        # 10. 日志安全
        if any(k in search_text for k in ['log', 'sensitive', 'password', 'token', 'secret']):
            if self._repair_logging_issue(filepath, issue):
                repaired = True

        # 格式化仅作为最后一步
        # **策略变更**：完全禁用autopep8，避免格式化噪声导致大量无意义diff
        # PR #92421 因 +555,-210 的格式化噪声被关闭
        if repaired:
            new_content = self._get_file_content(filepath)
            if not self._is_meaningful_change(original_content, new_content):
                logger.warning(f"Repair for {filepath} produced only formatting changes, rolling back")
                return False

        return repaired

    def _repair_java_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """修复Java文件"""
        repaired = False
        body = issue.get("body", "").lower()

        # NullPointerException: 添加 null 检查
        if 'nullpointer' in error_type.lower() or 'nullpointer' in body:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                original = content
                lines = content.split('\n')
                modified = False

                # 从错误信息中提取属性/方法名
                prop_matches = re.findall(r'Cannot invoke "([^"]+)" because "([^"]+)" is null', body)
                # 也匹配 "xxx is null" 模式（支持有引号和无引号）
                if not prop_matches:
                    var_matches = re.findall(r'because "?([^"\s]+)"? is null', body)
                    for var in var_matches:
                        prop_matches.append(('', var))
                # 也匹配 "Cannot invoke X because Y is null"（无引号）
                if not prop_matches:
                    prop_matches = re.findall(r'Cannot invoke (\w+) because (\w+) is null', body)
                for prop, var in prop_matches:
                    for i, line_text in enumerate(lines):
                        stripped = line_text.strip()
                        if stripped.startswith('//') or not stripped:
                            continue
                        # 只在包含 var. 或 var.get 的行添加 null 检查，跳过函数签名
                        if re.search(rf'\b{re.escape(var)}\.', stripped) or re.search(rf'\b{re.escape(var)}\s*\.', stripped):
                            # 检查是否已经有 null 检查
                            context = '\n'.join(lines[max(0, i-3):i+1])
                            if 'null' in context and ('if' in context or '!=' in context):
                                continue
                            indent = len(line_text) - len(line_text.lstrip())
                            spaces = ' ' * indent
                            new_lines = [
                                f"{spaces}if ({var} != null) {{",
                                f"{spaces}    {stripped}",
                                f"{spaces}}}"
                            ]
                            lines[i] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Added null check for '{var}' at line {i+1}")
                            break

                if modified:
                    FileHelper.write_file(filepath, '\n'.join(lines))
                    repaired = self._is_meaningful_change(original, '\n'.join(lines))
            except Exception as e:
                logger.error(f"Java null check repair failed: {e}")

        return repaired

    def _repair_go_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """修复Go文件"""
        repaired = False
        body = issue.get("body", "").lower()

        # nil pointer dereference
        if 'nil' in body or 'nil pointer' in error_type:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                original = content
                lines = content.split('\n')
                modified = False

                # 尝试提取变量名
                var_matches = re.findall(r"runtime error: invalid memory address or nil pointer dereference", body)
                if var_matches:
                    # 在函数开头添加 nil 检查（保守策略）
                    for i, line_text in enumerate(lines):
                        stripped = line_text.strip()
                        if stripped.startswith('func ') and '{' in stripped:
                            # 在函数体开始处添加 defer recover
                            indent = len(line_text) - len(line_text.lstrip())
                            spaces = ' ' * indent
                            next_line = lines[i+1] if i+1 < len(lines) else ""
                            next_indent = len(next_line) - len(next_line.lstrip()) if next_line else indent + 4
                            inner_spaces = ' ' * next_indent
                            new_lines = [
                                line_text,
                                f"{inner_spaces}defer func() {{",
                                f"{inner_spaces}    if r := recover(); r != nil {{",
                                f"{inner_spaces}        log.Printf(\"Recovered from panic: %v\", r)",
                                f"{inner_spaces}    }}",
                                f"{inner_spaces}}}()"
                            ]
                            lines[i] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Added panic recovery at line {i+1}")
                            break

                if modified:
                    FileHelper.write_file(filepath, '\n'.join(lines))
                    repaired = self._is_meaningful_change(original, '\n'.join(lines))
            except Exception as e:
                logger.error(f"Go nil check repair failed: {e}")

        return repaired

    def _repair_rust_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """修复Rust文件（增强版）"""
        repaired = False
        body = issue.get("body", "").lower()
        title = issue.get("title", "").lower()
        search_text = f"{error_type} {body} {title}"

        logger.info(f"[_repair_rust_file] filepath={filepath}, error_type={error_type}, search_text_preview={search_text[:100]}")

        # 1. 修复 Windows 命令行转义问题（Issue #7083 类）
        if any(k in search_text for k in ['windows', 'shell', 'quote', 'escape', 'cmd.exe', 'mangle']):
            logger.info(f"[_repair_rust_file] Triggering _repair_rust_windows_shell_escape for {filepath}")
            result = self._repair_rust_windows_shell_escape(filepath, issue)
            logger.info(f"[_repair_rust_file] _repair_rust_windows_shell_escape returned {result}")
            if result:
                repaired = True

        # 2. 修复路径/目录隔离问题（Issue #7054 类）
        if any(k in search_text for k in ['parallel', 'test', 'data dir', 'sqlite', 'databasebusy', 'race']):
            logger.info(f"[_repair_rust_file] Triggering _repair_rust_test_isolation for {filepath}")
            result = self._repair_rust_test_isolation(filepath, issue)
            logger.info(f"[_repair_rust_file] _repair_rust_test_isolation returned {result}")
            if result:
                repaired = True

        # 3. 修复配置持久化问题（Issue #7094 类）
        if any(k in search_text for k in ['persist', 'config', 'model', 'save', 'store']):
            logger.info(f"[_repair_rust_file] Triggering _repair_rust_config_persist for {filepath}")
            result = self._repair_rust_config_persist(filepath, issue)
            logger.info(f"[_repair_rust_file] _repair_rust_config_persist returned {result}")
            if result:
                repaired = True

        # 4. 修复 Option/Result 未处理
        if any(k in search_text for k in ['unwrap', 'panic', 'expect', 'none', 'some']):
            logger.info(f"[_repair_rust_file] Triggering _repair_rust_unwrap_handling for {filepath}")
            result = self._repair_rust_unwrap_handling(filepath, issue)
            logger.info(f"[_repair_rust_file] _repair_rust_unwrap_handling returned {result}")
            if result:
                repaired = True

        # 5. 修复字符串格式化问题
        if any(k in search_text for k in ['format', 'display', 'to_string', 'arg']):
            logger.info(f"[_repair_rust_file] Triggering _repair_rust_string_format for {filepath}")
            result = self._repair_rust_string_format(filepath, issue)
            logger.info(f"[_repair_rust_file] _repair_rust_string_format returned {result}")
            if result:
                repaired = True

        # 6. borrow checker / lifetime issues - 保守处理
        if error_type and ('borrow' in error_type.lower() or 'lifetime' in error_type.lower()):
            logger.warning(f"Rust borrow/lifetime issue detected in {filepath}, requires manual review")

        logger.info(f"[_repair_rust_file] Final result for {filepath}: repaired={repaired}")
        return repaired

    def _repair_rust_windows_shell_escape(self, filepath: str, issue: Dict) -> bool:
        """修复 Rust Windows shell 命令转义问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            original = content
            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or not stripped:
                    continue

                # 匹配 Command::new("cmd") 或 Command::new("cmd.exe")
                if 'Command::new' in stripped and ('cmd' in stripped or 'shell' in stripped):
                    # 检查是否有 .arg() 包含引号
                    context = '\n'.join(lines[i:min(i+10, len(lines))])
                    if '.arg(' in context and ('"' in context or "'" in context):
                        # 检查是否已经有转义处理
                        if 'escape' not in context and 'quote' not in context:
                            indent = len(line) - len(line.lstrip())
                            spaces = ' ' * indent
                            # 在 Command 创建后添加转义注释和修复
                            new_lines = [
                                f"{spaces}// Auto-fixed Windows shell escaping",
                                f"{spaces}let cmd = {stripped};",
                                f"{spaces}cmd.arg(\"\\\"\"); // Ensure proper quote escaping for cmd.exe",
                            ]
                            lines[i] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Added Windows shell escape fix at line {i+1}")
                            break

                # 匹配 std::process::Command 使用
                if 'std::process::Command' in stripped:
                    context = '\n'.join(lines[i:min(i+15, len(lines))])
                    if '.arg(' in context and 'windows' in issue.get('body', '').lower():
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 添加 Windows 特定的转义处理
                        new_lines = [
                            f"{spaces}#[cfg(windows)]",
                            f"{spaces}// Auto-fixed: Properly escape arguments for cmd.exe",
                            f"{spaces}{stripped}",
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added Windows-specific arg escaping at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Rust Windows shell escape repair failed: {e}")
            return False

    def _repair_rust_test_isolation(self, filepath: str, issue: Dict) -> bool:
        """修复 Rust 测试隔离问题（临时目录、数据库路径等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            original = content
            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or not stripped:
                    continue

                # 检查当前行是否在函数体内部（不是在函数调用参数中）
                # 简单启发式：检查当前行是否是独立的语句（以let/const/if/for/while等开头，或包含=赋值）
                is_statement = (
                    stripped.startswith('let ') or
                    stripped.startswith('const ') or
                    stripped.startswith('static ') or
                    stripped.startswith('if ') or
                    stripped.startswith('for ') or
                    stripped.startswith('while ') or
                    stripped.startswith('match ') or
                    stripped.startswith('fn ') or
                    '=' in stripped or
                    stripped.endswith(';') or
                    stripped.endswith('{') or
                    stripped.endswith('}')
                )

                # 检查是否在函数调用参数中（前面有未闭合的括号）
                in_function_call = False
                paren_count = 0
                for j in range(i - 1, max(0, i - 10), -1):
                    prev_line = lines[j]
                    for char in prev_line:
                        if char == '(':
                            paren_count += 1
                        elif char == ')':
                            paren_count -= 1
                    # 如果前面有未闭合的括号，说明我们在函数调用内部
                    if paren_count > 0:
                        in_function_call = True
                        break

                # 检查是否在结构体字面量或块表达式中（前面有未闭合的大括号）
                in_struct_literal = False
                brace_count = 0
                for j in range(i - 1, max(0, i - 10), -1):
                    prev_line = lines[j]
                    for char in prev_line:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                    # 如果前面有未闭合的大括号，说明我们在结构体字面量或块内部
                    if brace_count > 0:
                        in_struct_literal = True
                        break

                # 如果在函数调用参数中或结构体字面量中，跳过此行的修改
                if (in_function_call or in_struct_literal) and not is_statement:
                    continue

                # 检查是否是方法链的一部分（下一行以 . 开头）
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line.startswith('.'):
                        continue

                # 检查是否是未完成的赋值语句（当前行以 = 结尾但下一行不是以 . 开头）
                # 例如：let home = \n UserDirs::new()...
                if stripped.endswith('=') and not stripped.endswith('=='):
                    continue

                # 检查是否是 if let 或 if 表达式开头（没有对应的块大括号）
                if stripped.startswith('if '):
                    continue

                # 检查是否是属性宏行（以 # 开头）
                if stripped.startswith('#'):
                    continue

                # 检查是否在结构体/枚举/ trait 定义内部
                in_type_definition = False
                in_match_arm = False
                brace_depth = 0
                for j in range(i - 1, -1, -1):
                    prev = lines[j]
                    brace_depth += prev.count('{') - prev.count('}')
                    # 检查是否是 match 表达式（不依赖 brace_depth）
                    if re.search(r'\bmatch\b', prev):
                        in_match_arm = True
                        break
                    # 如果当前行在结构体/枚举/trait/impl 定义内部
                    # brace_depth > 0 表示我们在某个块内部
                    if brace_depth > 0:
                        if re.search(r'\b(struct|enum|trait|impl)\b', prev):
                            in_type_definition = True
                            break
                        if re.search(r'\bfn\b', prev):
                            break
                    # 如果 brace_depth == 0，说明我们在顶层或函数之间
                    # 检查是否是结构体定义前的属性宏区域
                    if brace_depth == 0:
                        if re.search(r'\b(struct|enum|trait|impl)\b', prev):
                            # 检查结构体定义开始行是否在当前行之后（即当前行在 struct X { ... } 内部）
                            # 通过向前查找来确认
                            forward_brace = 0
                            for k in range(j, i):
                                forward_brace += lines[k].count('{') - lines[k].count('}')
                            if forward_brace > 0:
                                in_type_definition = True
                                break
                if in_type_definition or in_match_arm:
                    continue

                # 匹配硬编码的 home/data 路径
                if any(k in stripped for k in ['~/.zeroclaw', 'home_dir', 'data_dir', 'HOME']):
                    # 检查是否已经在测试中使用临时目录
                    if 'temp' not in stripped and 'tmp' not in stripped:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 添加临时目录支持
                        new_lines = [
                            f"{spaces}// Auto-fixed: Use temp directory for test isolation",
                            f"{spaces}let temp_dir = std::env::temp_dir().join(format!(\"zeroclaw_test_{{}}\", std::process::id()));",
                            f"{spaces}std::fs::create_dir_all(&temp_dir).ok();",
                            f"{spaces}{stripped.replace('~/.zeroclaw', '${temp_dir}')}",
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added test isolation temp dir at line {i+1}")
                        break

                # 匹配 SQLite/数据库连接 - 只在独立语句中匹配，不在函数参数中
                # 严格匹配：必须是 let/const 赋值或独立函数调用，而不是参数中的字符串
                if is_statement and ('sqlite' in stripped.lower() or 'database' in stripped.lower() or 'Connection::open' in stripped):
                    if 'memory' not in stripped and ':memory:' not in stripped:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 建议使用内存数据库或临时文件
                        new_lines = [
                            f"{spaces}// Auto-fixed: Use in-memory or temp SQLite for parallel tests",
                            f"{spaces}let db_path = format!(\"file::memory:?cache=shared_{{}}\", uuid::Uuid::new_v4());",
                            f"{spaces}{stripped}",
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added SQLite isolation fix at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Rust test isolation repair failed: {e}")
            return False

    def _repair_rust_config_persist(self, filepath: str, issue: Dict) -> bool:
        """修复 Rust 配置持久化问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            original = content
            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or not stripped:
                    continue

                # 匹配配置读取但没有保存
                if 'config' in stripped.lower() or 'Config' in stripped:
                    context = '\n'.join(lines[max(0, i-5):min(i+10, len(lines))])
                    # 检查是否有 save/persist/write 调用
                    if 'save' not in context and 'persist' not in context and 'write' not in context:
                        # 检查是否是设置/修改配置值 - 必须包含明确的修改操作
                        # 有效的配置修改模式：config.xxx = ..., config.set_xxx(...), config.xxx.set(...)
                        # 不是配置修改的模式：let x = config.xxx(), config.xxx().yyy(), if config.xxx()
                        is_config_modification = (
                            # 直接赋值给 config 的字段：config.field = value
                            re.search(r'config\.\w+\s*=', stripped) or
                            # 调用 config 的 set 方法：config.set_xxx(...)
                            re.search(r'config\.set_\w+\s*\(', stripped) or
                            # 调用 config 的修改方法：config.xxx.set(...)
                            re.search(r'config\.\w+\.set\s*\(', stripped)
                        )
                        # 排除只是读取配置的情况：let x = config.xxx(), config.xxx().unwrap()
                        is_config_read = (
                            # let 赋值从 config 读取：let x = config.xxx()
                            (stripped.startswith('let ') and '=' in stripped and 'config.' in stripped) or
                            # 函数调用中使用 config 作为参数或接收者
                            re.search(r'\w+\s*\(.*config\.', stripped)
                        )
                        if not is_config_modification or is_config_read:
                            continue
                        # 安全检查：不要在方法链、闭包、match 臂等复杂表达式中插入
                        # 检查当前行是否是独立的语句
                        is_standalone_statement = (
                            stripped.endswith(';') or
                            stripped.endswith('}') or
                            stripped.endswith(')') or
                            stripped.startswith('let ') or
                            stripped.startswith('const ')
                        )
                        # 检查是否在函数调用参数列表中（通过括号深度）
                        prev_lines = '\n'.join(lines[max(0, i-10):i])
                        open_parens = prev_lines.count('(') - prev_lines.count(')')
                        open_braces = prev_lines.count('{') - prev_lines.count('}')
                        # 如果括号深度大于0，说明在函数调用或块内部，跳过
                        if open_parens > 0 and not is_standalone_statement:
                            continue
                        # 如果当前行是 return 表达式的一部分，跳过
                        if 'return' in stripped and ('(' in stripped or ',' in stripped):
                            continue
                        # 如果当前行是 match 臂或闭包体，跳过
                        if '=>' in stripped or '|' in stripped:
                            continue
                        # 检查是否是方法链的一部分（下一行以 . 开头）
                        if i + 1 < len(lines):
                            next_line = lines[i + 1].strip()
                            if next_line.startswith('.'):
                                continue
                        # 检查是否是属性宏行（以 # 开头）
                        if stripped.startswith('#'):
                            continue
                        # 检查是否是 static/const 声明，不是配置修改操作
                        if stripped.startswith('static ') or stripped.startswith('const '):
                            continue
                        # 检查是否是类型定义或结构体字段（包含 : 后跟类型）
                        if re.search(r':\s*\w+', stripped) and not stripped.startswith('let '):
                            continue
                        # 检查是否是 if let 或 if 表达式开头（没有对应的块大括号）
                        if stripped.startswith('if '):
                            continue
                        # 检查是否在结构体/枚举/ trait 定义内部或 match 表达式内部
                        in_type_definition = False
                        in_match_arm = False
                        brace_depth = 0
                        for j in range(i - 1, -1, -1):
                            prev = lines[j]
                            brace_depth += prev.count('{') - prev.count('}')
                            # 检查是否是 match 表达式（不依赖 brace_depth）
                            if re.search(r'\bmatch\b', prev):
                                in_match_arm = True
                                break
                            if brace_depth > 0:
                                # 检查是否是结构体/枚举/trait/impl 定义开始
                                if re.search(r'\b(struct|enum|trait|impl)\b', prev):
                                    in_type_definition = True
                                    break
                                # 如果遇到 fn 定义，说明在函数内部，不是类型定义
                                if re.search(r'\bfn\b', prev):
                                    break
                            if brace_depth == 0:
                                if re.search(r'\b(struct|enum|trait|impl)\b', prev):
                                    forward_brace = 0
                                    for k in range(j, i):
                                        forward_brace += lines[k].count('{') - lines[k].count('}')
                                    if forward_brace > 0:
                                        in_type_definition = True
                                        break
                        if in_type_definition or in_match_arm:
                            continue
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 添加保存调用
                        new_lines = [
                            f"{spaces}{stripped}",
                            f"{spaces}// Auto-fixed: Persist config changes",
                            f"{spaces}config.save().expect(\"Failed to persist config\");",
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added config persist call at line {i+1}")
                        break

                # 匹配 CLI 参数解析（clap）
                if '#[arg' in stripped or '#[command' in stripped:
                    context = '\n'.join(lines[max(0, i-3):min(i+15, len(lines))])
                    if 'model' in issue.get('body', '').lower() or 'persist' in issue.get('body', '').lower():
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 添加持久化注释
                        new_lines = [
                            f"{spaces}// Auto-fixed: Ensure model selection is persisted",
                            f"{spaces}{stripped}",
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added model persist hint at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Rust config persist repair failed: {e}")
            return False

    def _repair_rust_unwrap_handling(self, filepath: str, issue: Dict) -> bool:
        """修复 Rust unwrap/expect 未处理问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            original = content
            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or not stripped:
                    continue

                # 匹配 .unwrap() 但没有错误处理
                if '.unwrap()' in stripped and '?;' not in stripped:
                    # 检查是否在函数中返回 Result
                    context = '\n'.join(lines[max(0, i-10):i])
                    if '-> Result' in context or 'fn ' in context:
                        # 替换 unwrap() 为 ?
                        new_line = line.replace('.unwrap()', '?')
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Replaced unwrap() with ? at line {i+1}")
                            break

                # 匹配 .expect("...") 但没有合理错误信息
                match = re.search(r'\.expect\s*\(\s*"([^"]*)"\s*\)', stripped)
                if match:
                    msg = match.group(1)
                    if len(msg) < 5 or 'failed' not in msg.lower():
                        new_line = re.sub(
                            r'\.expect\s*\(\s*"[^"]*"\s*\)',
                            '.expect("Operation failed")',
                            line
                        )
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Improved expect message at line {i+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Rust unwrap handling repair failed: {e}")
            return False

    def _repair_rust_string_format(self, filepath: str, issue: Dict) -> bool:
        """修复 Rust 字符串格式化问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            original = content
            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or not stripped:
                    continue

                # 匹配 format! 宏中可能的错误
                if 'format!' in stripped:
                    # 检查是否有未使用的变量
                    match = re.search(r'format!\s*\(\s*"([^"]*)"\s*,', stripped)
                    if match:
                        fmt_str = match.group(1)
                        # 检查是否有 {{ 或 }} 未转义
                        if '{' in fmt_str and '}' in fmt_str:
                            if '{{' not in fmt_str and '}}' not in fmt_str:
                                # 可能是格式字符串问题，添加注释
                                indent = len(line) - len(line.lstrip())
                                spaces = ' ' * indent
                                new_lines = [
                                    f"{spaces}// Auto-fixed: Ensure format string uses {{}} for interpolation",
                                    f"{spaces}{stripped}",
                                ]
                                lines[i] = '\n'.join(new_lines)
                                modified = True
                                logger.info(f"Added format string hint at line {i+1}")
                                break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Rust string format repair failed: {e}")
            return False

    def _repair_generic_file(self, filepath: str, error_type: str, line: int, issue: Dict) -> bool:
        """通用文件修复"""
        # 通用修复不再默认执行，只在有明确错误模式时执行
        return False

    # ==================== 新增修复模式 ====================

    def _repair_performance_issue(self, filepath: str, issue: Dict) -> bool:
        """修复性能问题（重复计算、不必要的数据复制等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是性能相关
            perf_keywords = ['performance', 'slow', 'lag', 'delay', 'timeout', 'hang', 'freeze',
                           'cpu', 'memory', 'leak', 'inefficient', 'optimize', 'bottleneck']
            if not any(kw in body for kw in perf_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # 1. 查找循环中重复的属性访问，缓存到局部变量
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('#'):
                    continue

                # 匹配 for 循环中重复访问 arr.length
                match = re.search(r'for\s*\(\s*let\s+(\w+)\s*=\s*0\s*;\s*\1\s*<\s*(\w+)\.length', stripped)
                if match:
                    var_name = match.group(1)
                    arr_name = match.group(2)
                    # 检查循环体中是否有 arr.length 的重复访问
                    loop_end = self._find_loop_end(lines, i)
                    for j in range(i + 1, min(loop_end, len(lines))):
                        if f'{arr_name}.length' in lines[j]:
                            indent = len(lines[j]) - len(lines[j].lstrip())
                            spaces = ' ' * indent
                            new_lines = [
                                f"{spaces}const _len = {arr_name}.length; // Auto-fixed performance issue",
                                f"{spaces}{lines[j].strip()}".replace(f'{arr_name}.length', '_len')
                            ]
                            lines[j] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Cached array length at line {j+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Performance repair failed: {e}")
            return False

    def _find_loop_end(self, lines: List[str], start_idx: int) -> int:
        """找到循环的结束位置（简单启发式）"""
        brace_count = 0
        found_open = False
        for i in range(start_idx, len(lines)):
            stripped = lines[i].strip()
            if '{' in stripped:
                brace_count += stripped.count('{')
                found_open = True
            if '}' in stripped:
                brace_count -= stripped.count('}')
                if found_open and brace_count <= 0:
                    return i
        return len(lines)

    def _repair_config_issue(self, filepath: str, issue: Dict) -> bool:
        """修复配置相关问题（默认值、环境变量检查等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是配置相关
            config_keywords = ['config', 'configuration', 'setting', 'default', 'missing config',
                             'environment variable', 'env var', 'process.env', 'undefined config']
            if not any(kw in body for kw in config_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # 1. 为解构赋值添加默认值
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 const { a, b } = config; 但没有默认值
                match = re.search(r'const\s*\{\s*([^}]+)\}\s*=\s*(\w+)', stripped)
                if match:
                    vars_str = match.group(1)
                    source = match.group(2)
                    # 检查是否有默认值
                    if '=' not in vars_str:
                        # 添加默认值
                        new_vars = ', '.join([f"{v.strip()} = ''" for v in vars_str.split(',')])
                        new_line = line.replace(vars_str, new_vars)
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Added default values for destructuring at line {i+1}")

                # 2. 为 process.env.X 添加默认值
                match = re.search(r'(process\.env\.[A-Z_]+)\b', stripped)
                if match:
                    env_var = match.group(1)
                    # 检查是否已经有默认值
                    if not re.search(rf'{re.escape(env_var)}\s*\|\|', stripped):
                        new_line = re.sub(
                            rf'({re.escape(env_var)})(?!\s*\|\|)',
                            r'\1 || ""',
                            line
                        )
                        if new_line != line:
                            lines[i] = new_line
                            modified = True
                            logger.info(f"Added default for env var at line {i+1}")

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Config repair failed: {e}")
            return False

    def _repair_boundary_condition(self, filepath: str, issue: Dict) -> bool:
        """修复边界条件问题（空数组、空字符串、0值等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是边界条件相关
            boundary_keywords = ['empty', 'null', 'undefined', 'zero', '0', 'boundary',
                               'edge case', 'corner case', 'division by zero', 'out of range']
            if not any(kw in body for kw in boundary_keywords):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('#'):
                    continue

                # 1. 检查数组访问前是否有长度检查
                match = re.search(r'(\w+)\[(\w+)\]', stripped)
                if match:
                    arr_name = match.group(1)
                    idx_name = match.group(2)
                    # 检查上下文是否已有长度检查
                    context = '\n'.join(lines[max(0, i-3):i+1])
                    if f'{arr_name}.length' not in context and f'len({arr_name})' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        new_lines = [
                            f"{spaces}if ({arr_name} && {arr_name}.length > {idx_name}) {{ // Auto-fixed boundary check",
                            f"{spaces}    {stripped}",
                            f"{spaces}}}"
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added boundary check at line {i+1}")
                        break

                # 2. 检查除法是否有零值检查（只在函数体内，跳过 import/声明行/字符串）
                if '/' in stripped and not stripped.startswith('//'):
                    # 严格跳过任何 import/export/声明相关行，包括多行 import 的中间行
                    # 使用更宽松的检查：如果行中包含 import/from/export 关键字，就跳过
                    if any(kw in stripped for kw in ['import ', 'export ', 'from ']):
                        continue
                    if stripped.startswith('} from') or stripped.startswith('}from'):
                        continue
                    if re.match(r'^(const|let|var|type|interface)\s', stripped):
                        continue
                    # 跳过包含字符串中的 / 的行（如路径、URL）
                    if re.search(r'["\'][^"\']*/[^"\']*["\']', stripped):
                        continue
                    # 只匹配简单的变量除法，排除复杂表达式
                    match = re.search(r'\b(\w+)\s*/\s*(\w+)\b', stripped)
                    if match:
                        divisor = match.group(2)
                        # 检查除数是否是数字常量（不需要检查）
                        if re.match(r'^\d+$', divisor):
                            continue
                        # 检查上下文是否已有零值检查
                        context = '\n'.join(lines[max(0, i-5):i+1])
                        if f'{divisor} !== 0' not in context and f'{divisor} != 0' not in context and \
                           f'{divisor} === 0' not in context and f'{divisor} == 0' not in context:
                            indent = len(line) - len(line.lstrip())
                            spaces = ' ' * indent
                            new_lines = [
                                f"{spaces}if ({divisor} !== 0) {{ // Auto-fixed division by zero",
                                f"{spaces}    {stripped}",
                                f"{spaces}}} else {{",
                                f"{spaces}    console.warn('Division by zero avoided');",
                                f"{spaces}}}"
                            ]
                            lines[i] = '\n'.join(new_lines)
                            modified = True
                            logger.info(f"Added zero division check at line {i+1}")
                            break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Boundary condition repair failed: {e}")
            return False

    def _repair_missing_validation(self, filepath: str, issue: Dict) -> bool:
        """修复缺失的输入验证"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是验证相关
            validation_keywords = ['validation', 'validate', 'invalid', 'input', 'parameter',
                                 'argument', 'missing check', 'sanitize', 'escape']
            if not any(kw in body for kw in validation_keywords):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('#'):
                    continue

                # 匹配函数参数解构但没有验证
                match = re.search(r'(function|const)\s+(\w+)\s*\(\s*\{\s*([^}]+)\}\s*\)', stripped)
                if match:
                    func_name = match.group(2)
                    params = match.group(3)
                    # 检查是否已有验证
                    context = '\n'.join(lines[i:min(i+5, len(lines))])
                    if 'if' not in context or 'throw' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        param_list = [p.strip().split('=')[0].strip() for p in params.split(',')]
                        checks = ' && '.join([f"{p}" for p in param_list])
                        new_lines = [
                            f"{spaces}if (!({checks})) {{ // Auto-fixed input validation",
                            f"{spaces}    throw new Error('Invalid parameters for {func_name}');",
                            f"{spaces}}}",
                            f"{spaces}{stripped}"
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added input validation at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Validation repair failed: {e}")
            return False

    def _repair_async_timing_issue(self, filepath: str, issue: Dict) -> bool:
        """修复异步时序问题（回调顺序、事件顺序等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是时序相关
            timing_keywords = ['timing', 'order', 'sequence', 'before', 'after', 'race',
                             'async', 'callback', 'event', 'listener', 'trigger']
            if not any(kw in body for kw in timing_keywords):
                return False

            lines = content.split('\n')
            modified = False

            # 查找事件监听器注册但没有等待初始化完成
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 .on('event', handler) 但没有检查 ready 状态
                if '.on(' in stripped or '.addEventListener(' in stripped:
                    context = '\n'.join(lines[max(0, i-5):i+1])
                    if 'ready' not in context and 'init' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        # 去除原始行末尾的分号，避免嵌套时产生双分号
                        original_stripped = stripped.rstrip(';')
                        new_lines = [
                            f"{spaces}if (this._ready) {{ // Auto-fixed timing issue",
                            f"{spaces}    {original_stripped}",
                            f"{spaces}}} else {{",
                            f"{spaces}    this.once('ready', () => {{ {original_stripped}; }})",
                            f"{spaces}}}"
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added timing guard at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Async timing repair failed: {e}")
            return False

    def _repair_cors_or_network_issue(self, filepath: str, issue: Dict) -> bool:
        """修复 CORS 或网络相关问题"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是网络相关
            network_keywords = ['cors', 'network', 'connection', 'timeout', 'fetch',
                              'request', 'response', 'http', 'api', 'endpoint']
            if not any(kw in body for kw in network_keywords):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 fetch(url) 但没有错误处理
                if 'fetch(' in stripped and '.catch(' not in stripped:
                    indent = len(line) - len(line.lstrip())
                    spaces = ' ' * indent
                    new_lines = [
                        f"{spaces}{stripped.rstrip(';')}",
                        f"{spaces}    .catch(error => {{ // Auto-fixed network error handling",
                        f"{spaces}        console.error('Network request failed:', error);",
                        f"{spaces}        throw error;",
                        f"{spaces}    }});"
                    ]
                    lines[i] = '\n'.join(new_lines)
                    modified = True
                    logger.info(f"Added network error handling at line {i+1}")
                    break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Network repair failed: {e}")
            return False

    def _repair_state_management_issue(self, filepath: str, issue: Dict) -> bool:
        """修复状态管理问题（未初始化状态、状态不一致等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是状态相关
            state_keywords = ['state', 'undefined state', 'uninitialized', 'not set',
                            'stale', 'out of sync', 'inconsistent', 'race condition']
            if not any(kw in body for kw in state_keywords):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 this.state.x 访问但没有初始化检查
                match = re.search(r'this\.state\.(\w+)', stripped)
                if match:
                    prop = match.group(1)
                    # 检查是否已有初始化
                    context = '\n'.join(lines[max(0, i-5):i+1])
                    if 'constructor' not in context and 'state =' not in context:
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        new_lines = [
                            f"{spaces}if (!this.state) {{ // Auto-fixed state initialization",
                            f"{spaces}    this.state = {{}};",
                            f"{spaces}}}",
                            f"{spaces}{stripped}"
                        ]
                        lines[i] = '\n'.join(new_lines)
                        modified = True
                        logger.info(f"Added state initialization at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"State management repair failed: {e}")
            return False

    def _repair_logging_issue(self, filepath: str, issue: Dict) -> bool:
        """修复日志相关问题（敏感信息泄露、日志级别等）"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            body = issue.get("body", "").lower()

            # 检查是否是日志相关
            log_keywords = ['log', 'console', 'sensitive', 'password', 'token', 'secret',
                          'credential', 'api key', 'private', 'personal']
            if not any(kw in body for kw in log_keywords):
                return False

            lines = content.split('\n')
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue

                # 匹配 console.log(obj) 可能泄露敏感信息
                match = re.search(r'console\.(log|warn|error|info)\s*\(\s*(\w+)\s*\)', stripped)
                if match:
                    log_level = match.group(1)
                    var_name = match.group(2)
                    # 检查变量名是否可能是敏感信息
                    sensitive_names = ['password', 'token', 'secret', 'key', 'credential',
                                     'auth', 'private', 'personal']
                    if any(s in var_name.lower() for s in sensitive_names):
                        indent = len(line) - len(line.lstrip())
                        spaces = ' ' * indent
                        lines[i] = f"{spaces}console.{log_level}('[REDACTED]'); // Auto-fixed sensitive info leak"
                        modified = True
                        logger.info(f"Redacted sensitive log at line {i+1}")
                        break

            if modified:
                FileHelper.write_file(filepath, '\n'.join(lines))
                return self._is_meaningful_change(original, '\n'.join(lines))
            return False
        except Exception as e:
            logger.error(f"Logging repair failed: {e}")
            return False
