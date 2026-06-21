import os
import re
import json
from typing import List, Dict, Optional, Tuple

from src.common_utils.log_manager import get_logger
from src.common_utils.file_helper import FileHelper

logger = get_logger()


class CodeLocator:
    """缺陷代码定位：基于语法树+关键词匹配，精准锁定缺陷代码文件与行号"""

    # 语言对应的文件扩展名
    LANG_EXTENSIONS = {
        'python': ['.py'],
        'javascript': ['.js', '.jsx', '.mjs'],
        'typescript': ['.ts', '.tsx'],
        'java': ['.java'],
        'go': ['.go'],
        'rust': ['.rs'],
        'cpp': ['.cpp', '.cc', '.cxx', '.hpp', '.h'],
        'c': ['.c', '.h'],
        'csharp': ['.cs'],
        'ruby': ['.rb'],
        'php': ['.php'],
        'swift': ['.swift'],
        'kotlin': ['.kt'],
    }

    # 低相关性文件模式（测试、文档、配置、已发布运行时资源等）
    LOW_RELEVANCE_PATTERNS = [
        r'test[/\\]',
        r'tests[/\\]',
        r'__tests__[/\\]',
        r'\.test\.',
        r'\.spec\.',
        r'\.d\.ts$',
        r'\.stories\.',
        r'\.story\.',
        r'fixtures[/\\]',
        r'__snapshots__[/\\]',
        r'\.snap$',
        r'docs[/\\]',
        r'\.md$',
        r'\.txt$',
        r'\.json$',
        r'\.yaml$',
        r'\.yml$',
        r'\.config\.',
        r'\.css$',
        r'\.scss$',
        r'\.less$',
        r'\.html$',
        r'\.svg$',
        r'\.png$',
        r'\.jpg$',
        r'\.gif$',
        r'\.ico$',
        r'\.woff',
        r'\.ttf',
        r'\.eot',
        r'dist[/\\]',
        r'build[/\\]',
        r'node_modules[/\\]',
        r'vendor[/\\]',
        r'\.min\.js$',
        r'\.bundle\.js$',
        # 已发布的运行时资源 / 生成文件
        r'extensions/diffs/assets/viewer-runtime\.js$',
        r'assets/chrome-extension/.*\.js$',
        r'apps/shared/OpenClawKit/Tools/CanvasA2UI/bootstrap\.js$',
        r'apps/shared/OpenClawKit/Tools/CanvasA2UI/rolldown\.config\.mjs$',
        r'openclaw\.mjs$',
    ]

    def __init__(self, project_path: str):
        self.project_path = project_path

    def _is_low_relevance_file(self, filepath: str) -> bool:
        """检查文件是否为低相关性文件（测试、文档、配置等）"""
        normalized = filepath.replace('\\', '/')
        for pattern in self.LOW_RELEVANCE_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return True
        return False

    # 运行时/生成文件路径模式（低相关性，通常不应修改）
    RUNTIME_ASSET_PATTERNS = [
        r'\.runtime\.[jt]s$',
        r'\.live\.test\.[jt]s$',
        r'\.qa\.[jt]s$',
        r'\.bundle\.[jt]s$',
        r'\.min\.[jt]s$',
        r'assets/.*\.js$',
        r'dist/.*',
        r'build/.*',
        r'extensions/.*\.js$',
    ]

    # 高价值源代码目录（优先选择）
    HIGH_VALUE_SOURCE_PATTERNS = [
        r'/src/',
        r'/source/',
        r'/lib/',
        r'/core/',
        r'/engine/',
        r'/runtime/',
        r'/packages/[^/]+/src/',
        r'/apps/[^/]+/Sources/',
    ]

    def _is_runtime_asset_file(self, filepath: str) -> bool:
        """检查文件是否是运行时/生成资源文件（不应修改）"""
        normalized = filepath.replace('\\', '/')
        for pattern in self.RUNTIME_ASSET_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return True
        return False

    def _is_high_value_source_file(self, filepath: str) -> bool:
        """检查文件是否在高价值源代码目录中"""
        normalized = filepath.replace('\\', '/')
        for pattern in self.HIGH_VALUE_SOURCE_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return True
        return False

    def _score_file_relevance(self, filepath: str, issue: Dict) -> int:
        """
        计算文件与Issue的相关性得分
        得分越高表示越相关
        """
        score = 0
        normalized = filepath.replace('\\', '/').lower()
        basename = os.path.basename(filepath).lower()

        # 1. 堆栈跟踪中的文件（最高优先级）
        stack_trace_files = issue.get("stack_trace_files", [])
        for stf in stack_trace_files:
            if stf.lower() in normalized or os.path.basename(stf).lower() == basename:
                score += 100
                break

        # 2. file_refs 中的文件
        file_refs = issue.get("file_refs", [])
        for ref in file_refs:
            ref_lower = ref.lower()
            if ref_lower in normalized or os.path.basename(ref).lower() == basename:
                score += 80
                break

        # 3. 函数名匹配
        affected_functions = issue.get("affected_functions", [])
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
            for func in affected_functions:
                func_lower = func.lower()
                if func_lower in content:
                    # 检查是否是函数定义
                    patterns = [
                        rf'\b(def|function|func|fn)\s+{re.escape(func_lower)}\b',
                        rf'\b{re.escape(func_lower)}\s*[:\(]',
                        rf'const\s+{re.escape(func_lower)}\s*=',
                    ]
                    for pattern in patterns:
                        if re.search(pattern, content):
                            score += 50
                            break
                    else:
                        score += 20  # 只是提到函数名
        except Exception:
            pass

        # 4. 错误类型关键词匹配
        error_type = (issue.get("error_type") or "").lower()
        if error_type and error_type not in ('error', 'exception', 'bug', 'issue'):
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().lower()
                if error_type in content:
                    score += 30
            except Exception:
                pass

        # 5. 文件路径与Issue标题关键词匹配（增强版）
        title = (issue.get("title") or "").lower()
        title_words = [w for w in re.findall(r'\b[a-z]{3,}\b', title)
                       if w not in {'the', 'and', 'for', 'fix', 'bug', 'error', 'issue'}]
        for word in title_words:
            if word in normalized or word in basename:
                score += 10

        # 5.1 Issue特定关键词与文件路径的语义匹配（高权重）
        # 从issue标题和正文中提取特定技术关键词
        issue_text = f"{title} {(issue.get('body') or '').lower()}"
        specific_keywords = self._extract_specific_keywords(issue_text)
        for keyword, weight in specific_keywords:
            if keyword in normalized or keyword in basename:
                score += weight

        # 6. 语言匹配
        issue_lang = issue.get("language")
        if issue_lang:
            file_lang = self._detect_file_language(filepath)
            if file_lang == issue_lang:
                score += 15
            elif issue_lang in ('javascript', 'typescript') and file_lang in ('javascript', 'typescript'):
                score += 10

        # 7. 高价值源代码目录加分
        if self._is_high_value_source_file(filepath):
            score += 20

        # 8. 运行时/生成文件扣分（强烈 discourage 修改）
        if self._is_runtime_asset_file(filepath):
            score -= 300

        # 9. 低相关性文件扣分（测试、文档、配置等）
        if self._is_low_relevance_file(filepath):
            score -= 200

        return max(0, score)

    def _extract_specific_keywords(self, text: str) -> List[Tuple[str, int]]:
        """
        从issue文本中提取特定技术关键词及其权重
        返回: [(keyword, weight), ...]
        """
        keywords = []

        # 运行时相关关键词（高权重，用于区分embedded runtime vs QA runtime）
        runtime_keywords = [
            ('embedded', 40), ('runtime', 35), ('secondary', 30),
            ('agent', 25), ('memory', 30), ('path', 25),
            ('workspace', 25), ('bootstrap', 30), ('context', 25),
            ('read', 20), ('tool', 20), ('prompt', 20),
        ]
        for keyword, weight in runtime_keywords:
            if keyword in text:
                keywords.append((keyword, weight))

        # 模块/组件特定关键词
        module_keywords = [
            ('telegram', 30), ('chrome', 25), ('extension', 25),
            ('canvas', 25), ('openclaw', 20), ('kit', 20),
            ('diff', 20), ('viewer', 20), ('export', 20),
        ]
        for keyword, weight in module_keywords:
            if keyword in text:
                keywords.append((keyword, weight))

        return keywords

    def _normalize_file_ref(self, ref: str) -> str:
        """将GitHub URL或各种格式的文件引用转换为相对路径"""
        if not ref:
            return ""
        # 统一使用正斜杠
        ref = ref.replace('\\', '/')
        # 1. 移除 GitHub blob URL 模式: blob/{branch}/
        blob_match = re.search(r'/blob/[^/]+/(.+)$', ref)
        if blob_match:
            return blob_match.group(1)
        # 2. 移除 tree URL 模式: tree/{branch}/
        tree_match = re.search(r'/tree/[^/]+/(.+)$', ref)
        if tree_match:
            return tree_match.group(1)
        # 3. 移除 raw.githubusercontent.com 前缀
        if 'raw.githubusercontent.com' in ref:
            parts = ref.split('/')
            if len(parts) >= 5:
                return '/'.join(parts[4:])
        # 4. 移除 github.com/{owner}/{repo} 前缀 (如 com/openclaw/openclaw/...)
        # 匹配 owner/repo/ 后面跟着路径的模式
        github_prefix_match = re.search(r'(?:com/)?[^/]+/[^/]+/(.+)$', ref)
        if github_prefix_match:
            candidate = github_prefix_match.group(1)
            # 确保不是以 blob/tree/raw 开头
            if not re.match(r'^(blob|tree|raw)/', candidate):
                return candidate
        return ref

    def locate_by_file_refs(self, file_refs: List[str], language: str = None) -> List[str]:
        """根据文件引用定位实际文件路径（带验证）"""
        matched = []
        seen = set()
        for raw_ref in file_refs:
            # 标准化引用路径
            ref = self._normalize_file_ref(raw_ref)
            if not ref:
                continue
            # 跳过明显无效的路径
            if ref.startswith('//') or ref.startswith('/'):
                continue
            # 尝试直接拼接
            full_path = os.path.normpath(os.path.join(self.project_path, ref))
            norm = os.path.normpath(os.path.abspath(full_path)).lower()
            if norm in seen:
                continue
            if os.path.exists(full_path) and os.path.isfile(full_path):
                matched.append(full_path)
                seen.add(norm)
                continue
            #  basename回退：只在引用路径包含至少2个目录层级时尝试，
            #  避免 generic 文件名（如 index.js）匹配到大量无关文件
            ref_basename = os.path.basename(ref)
            ref_parts = ref.replace('\\', '/').split('/')
            if len(ref_parts) < 2:
                continue  # 单文件名太泛化，不尝试basename匹配
            for root, _, files in os.walk(self.project_path):
                for f in files:
                    if f == ref_basename:
                        candidate = os.path.join(root, f)
                        candidate_norm = os.path.normpath(os.path.abspath(candidate)).lower()
                        if candidate_norm in seen:
                            continue
                        # 验证路径的后几部分是否匹配
                        candidate_parts = candidate.replace('\\', '/').split('/')
                        if self._path_suffix_match(ref_parts, candidate_parts):
                            matched.append(candidate)
                            seen.add(candidate_norm)
                            break
        logger.info(f"Located {len(matched)} files from refs: {file_refs}")
        return matched

    def _path_suffix_match(self, ref_parts: List[str], candidate_parts: List[str]) -> bool:
        """检查候选路径是否以引用路径的后缀结尾"""
        if len(ref_parts) > len(candidate_parts):
            return False
        # 检查最后N个部分是否匹配
        ref_suffix = ref_parts[-min(3, len(ref_parts)):]
        cand_suffix = candidate_parts[-len(ref_suffix):]
        return ref_suffix == cand_suffix

    def locate_by_error_keyword(self, keyword: str, language: str = None) -> List[Tuple[str, int, str]]:
        """根据错误关键词在源码中搜索匹配行"""
        extensions = self._get_extensions(language)
        results = []
        files = FileHelper.list_files(self.project_path, extensions)
        for filepath in files:
            if self._is_low_relevance_file(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if keyword in line:
                            results.append((filepath, lineno, line.strip()))
            except Exception:
                continue
        logger.info(f"Found {len(results)} matches for keyword '{keyword}'")
        return results

    def locate_by_traceback(self, traceback_text: str) -> List[Dict]:
        """从堆栈信息中提取文件和行号"""
        results = []
        # 匹配多种堆栈格式
        patterns = [
            r'File "([^"]+)", line (\d+)',
            r'at\s+.+\s+\(([^:]+):(\d+):\d+\)',
            r'([^\s:]+):(\d+):\d+',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, traceback_text)
            for match in matches:
                if len(match) == 2:
                    filepath, lineno = match
                else:
                    continue
                # 验证文件是否在项目中
                basename = os.path.basename(filepath)
                local_path = os.path.join(self.project_path, basename)
                if not os.path.exists(local_path):
                    for root, _, files in os.walk(self.project_path):
                        if basename in files:
                            candidate = os.path.join(root, basename)
                            if not self._is_low_relevance_file(candidate):
                                local_path = candidate
                                break
                if os.path.exists(local_path) and not self._is_low_relevance_file(local_path):
                    results.append({
                        "file": local_path,
                        "line": int(lineno) if str(lineno).isdigit() else 0,
                        "original_file": filepath
                    })
        logger.info(f"Parsed {len(results)} locations from traceback")
        return results

    def find_function_around_line(self, filepath: str, line: int, language: str = None) -> Optional[str]:
        """查找指定行所在的函数/方法名"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            lang = language or self._detect_file_language(filepath)
            # 向上查找函数定义
            for i in range(line - 1, -1, -1):
                stripped = lines[i].strip()
                if lang in ('python',):
                    if stripped.startswith(("def ", "class ")):
                        return stripped
                elif lang in ('javascript', 'typescript'):
                    if re.match(r'^(function\s+\w+|const\s+\w+\s*=\s*(async\s*)?\(|\w+\s*\([^)]*\)\s*\{|class\s+\w+)', stripped):
                        return stripped
                elif lang in ('java', 'csharp', 'kotlin'):
                    if re.match(r'^(public|private|protected|static|\s)*(\w+\s+)+\w+\s*\(', stripped):
                        return stripped
                elif lang == 'go':
                    if stripped.startswith("func "):
                        return stripped
                elif lang == 'rust':
                    if stripped.startswith(("fn ", "impl ")):
                        return stripped
                elif lang == 'cpp':
                    if re.match(r'^[\w:]+\s+\w+\s*\(', stripped):
                        return stripped
            return None
        except Exception:
            return None

    def find_related_files(self, issue: Dict, max_files: int = 3) -> List[str]:
        """
        综合多种策略定位与Issue相关的文件，按相关性排序
        返回最相关的 max_files 个文件
        """
        all_files = []
        language = issue.get("language")

        # 跟踪是否有高置信度的文件定位成功（来自堆栈跟踪或file_refs）
        high_confidence_located = False

        # 1. 从堆栈跟踪定位（最高优先级）
        for snippet in issue.get("error_snippets", []):
            if "File " in snippet or "at " in snippet:
                tb_results = self.locate_by_traceback(snippet)
                if tb_results:
                    high_confidence_located = True
                all_files.extend([r["file"] for r in tb_results])

        # 2. 从 stack_trace_files 定位
        stack_trace_files = issue.get("stack_trace_files", [])
        if stack_trace_files:
            st_files = self.locate_by_file_refs(stack_trace_files, language)
            if st_files:
                high_confidence_located = True
            all_files.extend(st_files)

        # 3. 从 file_refs 定位
        if issue.get("file_refs"):
            ref_files = self.locate_by_file_refs(issue["file_refs"], language)
            if ref_files:
                high_confidence_located = True
            all_files.extend(ref_files)

        # 4. 从函数名定位（限制数量）
        # 只有在没有高置信度文件时，才使用函数名搜索；且要求函数名来自backtick引用（高置信度）
        affected_functions = issue.get("affected_functions", [])
        if affected_functions:
            # 如果已有高置信度文件，最多用1个函数名补充；否则最多用2个
            func_limit = 1 if high_confidence_located else 2
            for func_name in affected_functions[:func_limit]:
                keyword_results = self.locate_by_error_keyword(func_name, language)
                all_files.extend([r[0] for r in keyword_results[:3]])  # 每个函数最多3个文件

        # 去重（使用规范化路径，处理Windows下正反斜杠不一致的问题）
        seen = set()
        unique_files = []
        for f in all_files:
            norm = os.path.normpath(os.path.abspath(f)).lower()
            if norm not in seen:
                seen.add(norm)
                unique_files.append(f)
        all_files = unique_files

        # 过滤不存在的文件和低相关性文件（运行时资源、测试、文档等）
        all_files = [f for f in all_files if os.path.exists(f) and os.path.isfile(f) and not self._is_low_relevance_file(f)]

        # 新增：运行时资源文件拦截和源代码推断（新规则）
        # 如果定位到的文件全是运行时资源文件，尝试推断对应的源代码文件
        source_files = []
        runtime_files = []
        for f in all_files:
            if self._is_runtime_asset_file(f):
                runtime_files.append(f)
                # 尝试推断对应的源代码文件
                inferred = self._infer_source_file_from_runtime(f, issue)
                if inferred:
                    source_files.append(inferred)
                    logger.info(f"Inferred source file from runtime asset: {f} -> {inferred}")
            else:
                source_files.append(f)

        # 如果找到了源代码文件，优先使用源代码文件
        if source_files:
            all_files = source_files
            logger.info(f"Using inferred source files instead of runtime assets. Runtime assets skipped: {runtime_files}")
        elif runtime_files:
            # 如果只有运行时资源文件，标记为高风险
            logger.warning(f"Only runtime asset files found: {runtime_files}. This is a high-risk issue.")

        # 计算相关性得分并排序
        scored_files = []
        for filepath in all_files:
            score = self._score_file_relevance(filepath, issue)
            scored_files.append((filepath, score))

        scored_files.sort(key=lambda x: x[1], reverse=True)

        # 如果没有高置信度文件定位成功，提高得分门槛，避免选中无关文件
        min_score_threshold = 30 if high_confidence_located else 60
        result = [f for f, s in scored_files[:max_files] if s >= min_score_threshold]

        # 5. 如果仍然没有找到文件，尝试智能搜索策略
        if not result:
            result = self._smart_search_files(issue, max_files)

        logger.info(f"Found {len(result)} relevant files (scored top {max_files} from {len(all_files)} candidates, high_confidence={high_confidence_located})")
        for f, s in scored_files[:max_files]:
            logger.info(f"  {f}: score={s}")

        return result

    def _smart_search_files(self, issue: Dict, max_files: int = 3) -> List[str]:
        """
        智能搜索：当常规方法找不到文件时，使用项目结构分析和关键词搜索
        """
        logger.info("Using smart search strategy...")
        candidates = []

        title = issue.get("title", "").lower()
        body = issue.get("body", "").lower()
        error_type = (issue.get("error_type") or "").lower()
        error_context = issue.get("error_context", "").lower()

        # 从标题和正文中提取技术关键词（排除常见词）
        tech_keywords = self._extract_tech_keywords(title + " " + body + " " + error_context)

        # 从错误类型提取关键词
        if error_type:
            tech_keywords.append(error_type)

        # 搜索每个关键词
        for keyword in tech_keywords[:5]:  # 最多搜索5个关键词
            results = self.locate_by_error_keyword(keyword, issue.get("language"))
            for filepath, _, _ in results[:5]:  # 每个关键词最多5个结果
                if os.path.exists(filepath) and os.path.isfile(filepath) and not self._is_low_relevance_file(filepath):
                    score = self._score_file_relevance(filepath, issue)
                    # 额外加分：关键词在文件名中
                    if keyword.lower() in os.path.basename(filepath).lower():
                        score += 25
                    candidates.append((filepath, score))

        # 去重并排序
        seen = set()
        unique_candidates = []
        for filepath, score in candidates:
            if filepath not in seen:
                seen.add(filepath)
                unique_candidates.append((filepath, score))

        unique_candidates.sort(key=lambda x: x[1], reverse=True)
        result = [f for f, s in unique_candidates[:max_files] if s > 0]

        if result:
            logger.info(f"Smart search found {len(result)} files")
        else:
            logger.warning("Smart search found no files")

        return result

    def _extract_tech_keywords(self, text: str) -> List[str]:
        """从技术文本中提取有意义的关键词"""
        # 排除的常见词
        stop_words = {
            'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
            'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has',
            'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see',
            'two', 'who', 'boy', 'did', 'she', 'use', 'her', 'way', 'many',
            'oil', 'sit', 'set', 'run', 'eat', 'far', 'sea', 'eye', 'ago',
            'off', 'too', 'any', 'say', 'man', 'try', 'ask', 'end', 'why',
            'let', 'put', 'say', 'she', 'try', 'way', 'own', 'say', 'too',
            'old', 'tell', 'very', 'when', 'come', 'here', 'just', 'like',
            'long', 'make', 'over', 'such', 'take', 'than', 'them', 'well',
            'were', 'will', 'with', 'have', 'from', 'they', 'know', 'want',
            'been', 'good', 'much', 'some', 'time', 'very', 'when', 'come',
            'here', 'just', 'like', 'long', 'make', 'over', 'such', 'take',
            'than', 'them', 'well', 'were', 'bug', 'fix', 'error', 'issue',
            'problem', 'crash', 'fail', 'broken', 'wrong', 'expected',
            'actual', 'behavior', 'steps', 'reproduce', 'solution',
            'workaround', 'patch', 'github', 'openclaw'
        }

        # 匹配技术术语：驼峰命名、下划线命名、连字符命名
        patterns = [
            r'\b[A-Z][a-z]+[A-Z]\w*\b',  # CamelCase
            r'\b[a-z]+_[a-z_]+\b',  # snake_case
            r'\b[a-z]+-[a-z-]+\b',  # kebab-case
            r'\b[A-Z_]+\b',  # UPPER_CASE (constants)
            r'\b[a-z]+(?:\.[a-z]+)+\b',  # dot.notation
        ]

        keywords = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                match_lower = match.lower()
                if match_lower not in stop_words and len(match) >= 3:
                    keywords.add(match)

        # 也提取4个字母以上的单词
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
        for word in words:
            word_lower = word.lower()
            if word_lower not in stop_words:
                keywords.add(word)

        return list(keywords)

    def _infer_source_file_from_runtime(self, runtime_file: str, issue: Dict) -> Optional[str]:
        """
        从运行时资源文件推断对应的源代码文件。
        例如：telegram-live.runtime.ts -> src/telegram/ 目录下的相关文件
        """
        normalized = runtime_file.replace('\\', '/').lower()
        basename = os.path.basename(normalized)

        # 移除运行时相关后缀
        source_name = basename
        for suffix in ['.runtime.', '.bundle.', '.min.', '.qa.', '.live.']:
            if suffix in source_name:
                source_name = source_name.split(suffix)[0]

        # 移除扩展名
        source_name = os.path.splitext(source_name)[0]

        # 从文件名提取模块名（支持连字符分隔）
        module_names = []
        if '-' in source_name:
            parts = source_name.split('-')
            module_names.extend(parts)
        module_names.append(source_name)

        # 在源代码目录中搜索匹配的文件
        source_dirs = [
            os.path.join(self.project_path, 'src'),
            os.path.join(self.project_path, 'packages'),
            os.path.join(self.project_path, 'apps'),
            os.path.join(self.project_path, 'lib'),
            os.path.join(self.project_path, 'core'),
        ]

        candidates = []
        for source_dir in source_dirs:
            if not os.path.exists(source_dir):
                continue
            for root, _, files in os.walk(source_dir):
                for f in files:
                    if f.endswith(('.ts', '.tsx', '.js', '.jsx')):
                        f_lower = f.lower()
                        f_name = os.path.splitext(f_lower)[0]
                        full_path = os.path.join(root, f)
                        # 跳过运行时资源文件本身
                        if full_path.replace('\\', '/').lower() == normalized:
                            continue
                        # 匹配文件名或模块名
                        for module_name in module_names:
                            if module_name in f_name or module_name in root.lower():
                                candidates.append(full_path)
                                break

        # 如果Issue有关键词，进一步筛选
        if candidates and issue:
            title = (issue.get("title") or "").lower()
            body = (issue.get("body") or "").lower()
            issue_text = f"{title} {body}"

            # 提取核心关键词
            core_keywords = []
            runtime_patterns = [
                (r'embedded\s+runtime', 'embedded'),
                (r'qa\s+runtime', 'qa'),
                (r'live\s+runtime', 'live'),
                (r'telegram\s+runtime', 'telegram'),
                (r'secondary\s+agent', 'secondary'),
                (r'memory\s+path', 'memory'),
            ]
            for pattern, keyword in runtime_patterns:
                if re.search(pattern, issue_text):
                    core_keywords.append(keyword)

            # 优先选择包含核心关键词的文件
            if core_keywords:
                for keyword in core_keywords:
                    for candidate in candidates:
                        if keyword in candidate.lower():
                            return candidate

        # 返回第一个候选
        return candidates[0] if candidates else None

    def validate_files_relevance(self, files: List[str], issue: Dict) -> Tuple[bool, str]:
        """
        验证选中的文件是否确实与Issue相关
        返回: (是否通过, 失败原因)
        """
        if not files:
            return False, "No files selected"

        # 检查是否有高相关性文件（来自堆栈跟踪或file_refs）
        has_high_relevance = False
        for filepath in files:
            score = self._score_file_relevance(filepath, issue)
            if score >= 50:  # 来自堆栈跟踪或函数定义匹配
                has_high_relevance = True
                break

        if not has_high_relevance:
            return False, (
                "Selected files have low relevance to the issue. "
                "No stack trace match or function definition found. "
                f"Files: {files}"
            )

        # 检查文件内容是否包含错误相关关键词
        error_context = issue.get("error_context", "")
        if error_context:
            error_keywords = [w for w in re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', error_context)
                              if len(w) >= 4 and w.lower() not in {
                                  'error', 'exception', 'undefined', 'cannot', 'property',
                                  'module', 'found', 'expected', 'actual', 'value',
                                  'object', 'string', 'number', 'array', 'function'
                              }]
            for filepath in files:
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    for kw in error_keywords[:5]:
                        if kw in content:
                            return True, ""
                except Exception:
                    continue
            # 如果没有关键词匹配，但至少有一个高相关性文件，也允许通过
            if has_high_relevance:
                return True, ""
            return False, "Selected files do not contain error-related keywords"

        return True, ""

    def extract_function_body(self, filepath: str, func_name: str, language: str = None) -> Optional[str]:
        """提取指定函数的完整代码体"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lang = language or self._detect_file_language(filepath)
            if lang in ('javascript', 'typescript'):
                return self._extract_js_function(content, func_name)
            elif lang == 'python':
                return self._extract_python_function(content, func_name)
            return None
        except Exception:
            return None

    def _extract_js_function(self, content: str, func_name: str) -> Optional[str]:
        """提取JavaScript/TypeScript函数"""
        patterns = [
            rf'function\s+{re.escape(func_name)}\s*\([^)]*\)\s*\{{',
            rf'const\s+{re.escape(func_name)}\s*=\s*(?:async\s*)?\([^)]*\)\s*=\>\s*\{{?',
            rf'{re.escape(func_name)}\s*\([^)]*\)\s*\{{',
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                start = match.start()
                # 找到匹配的闭合括号
                brace_count = 0
                in_string = False
                string_char = None
                i = content.find('{', start)
                if i == -1:
                    continue
                while i < len(content):
                    char = content[i]
                    if not in_string:
                        if char in ('"', "'", '`'):
                            in_string = True
                            string_char = char
                        elif char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                return content[start:i+1]
                    else:
                        if char == string_char and content[i-1] != '\\':
                            in_string = False
                    i += 1
        return None

    def _extract_python_function(self, content: str, func_name: str) -> Optional[str]:
        """提取Python函数"""
        pattern = rf'^(def\s+{re.escape(func_name)}\s*\([^)]*\):)'
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            start = match.start()
            lines = content[start:].split('\n')
            result = [lines[0]]
            for line in lines[1:]:
                if line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                    break
                result.append(line)
            return '\n'.join(result)
        return None

    def _get_extensions(self, language: str = None) -> List[str]:
        """获取语言对应的文件扩展名"""
        if language and language in self.LANG_EXTENSIONS:
            return self.LANG_EXTENSIONS[language]
        # 返回所有支持的扩展名
        exts = []
        for e in self.LANG_EXTENSIONS.values():
            exts.extend(e)
        return exts

    def _detect_file_language(self, filepath: str) -> str:
        """根据文件扩展名检测语言"""
        ext = os.path.splitext(filepath)[1].lower()
        for lang, exts in self.LANG_EXTENSIONS.items():
            if ext in exts:
                return lang
        return 'unknown'
