import os
import re
from typing import Dict, List, Optional

from src.common_utils.log_manager import get_logger

logger = get_logger()


class IssueParser:
    """Issue内容解析：结构化拆解标题、正文、标签、报错内容，生成可分析数据结构"""

    # 扩展文件类型支持
    FILE_EXTENSIONS = r'(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|hpp|cs|rb|php|swift|kt|scala|r|m|mm)'

    # 常见非函数词过滤列表（扩展）
    # 添加数字、版本号、平台名称等无意义词
    COMMON_WORDS = {
        'if', 'for', 'while', 'return', 'const', 'let', 'var', 'function', 'class',
        'new', 'this', 'true', 'false', 'null', 'undefined', 'async', 'await',
        'import', 'from', 'export', 'default', 'try', 'catch', 'throw', 'finally',
        'switch', 'case', 'break', 'continue', 'do', 'else', 'extends', 'super',
        'static', 'public', 'private', 'protected', 'void', 'int', 'string',
        'number', 'boolean', 'any', 'null', 'nil', 'self', 'def', 'print',
        'console', 'log', 'error', 'warn', 'info', 'debug', 'trace', 'assert',
        'expect', 'describe', 'it', 'test', 'before', 'after', 'beforeEach',
        'afterEach', 'setup', 'teardown', 'given', 'when', 'then', 'and', 'or',
        'not', 'in', 'is', 'as', 'of', 'to', 'the', 'a', 'an', 'with', 'by',
        'on', 'at', 'from', 'into', 'through', 'during', 'before', 'after',
        'above', 'below', 'between', 'under', 'over', 'via', 'using', 'used',
        'use', 'get', 'set', 'add', 'remove', 'delete', 'create', 'update',
        'read', 'write', 'open', 'close', 'start', 'stop', 'run', 'exec',
        'call', 'apply', 'bind', 'map', 'filter', 'reduce', 'find', 'sort',
        'push', 'pop', 'shift', 'unshift', 'slice', 'splice', 'concat', 'join',
        'split', 'replace', 'match', 'search', 'index', 'length', 'size',
        'name', 'value', 'key', 'data', 'type', 'id', 'url', 'path', 'dir',
        'file', 'code', 'text', 'msg', 'message', 'err', 'error', 'e',
        'obj', 'object', 'arr', 'array', 'str', 'string', 'num', 'number',
        'bool', 'boolean', 'fn', 'func', 'callback', 'cb', 'handler',
        'listener', 'emitter', 'event', 'action', 'state', 'props', 'attr',
        'option', 'config', 'settings', 'params', 'args', 'arguments',
        'result', 'output', 'input', 'source', 'target', 'dest', 'destination',
        'root', 'base', 'home', 'cwd', 'pwd', 'env', 'environment',
        'process', 'module', 'package', 'lib', 'library', 'util', 'utils',
        'helper', 'helpers', 'common', 'shared', 'core', 'main', 'index',
        'init', 'initialize', 'setup', 'configure', 'build', 'compile',
        'parse', 'format', 'validate', 'check', 'verify', 'assert',
        'ensure', 'require', 'import', 'include', 'exclude', 'contain',
        'has', 'have', 'had', 'having', 'be', 'been', 'being', 'is',
        'are', 'was', 'were', 'will', 'would', 'should', 'shall', 'may',
        'might', 'can', 'could', 'must', 'need', 'dare', 'ought',
        'used', 'do', 'does', 'did', 'done', 'doing', 'get', 'got',
        'gotten', 'make', 'made', 'making', 'take', 'took', 'taken',
        'taking', 'come', 'came', 'coming', 'go', 'went', 'gone',
        'going', 'see', 'saw', 'seen', 'seeing', 'know', 'knew',
        'known', 'knowing', 'think', 'thought', 'thinking', 'say',
        'said', 'saying', 'tell', 'told', 'telling', 'ask', 'asked',
        'asking', 'give', 'gave', 'given', 'giving', 'find', 'found',
        'finding', 'feel', 'felt', 'feeling', 'become', 'became',
        'becoming', 'leave', 'left', 'leaving', 'put', 'putting',
        'mean', 'meant', 'meaning', 'keep', 'kept', 'keeping',
        'let', 'letting', 'begin', 'began', 'beginning', 'seem',
        'seemed', 'seeming', 'help', 'helped', 'helping', 'show',
        'showed', 'shown', 'showing', 'hear', 'heard', 'hearing',
        'play', 'played', 'playing', 'run', 'ran', 'running',
        'move', 'moved', 'moving', 'live', 'lived', 'living',
        'believe', 'believed', 'believing', 'bring', 'brought',
        'bringing', 'happen', 'happened', 'happening', 'stand',
        'stood', 'standing', 'lose', 'lost', 'losing', 'pay',
        'paid', 'paying', 'meet', 'met', 'meeting', 'include',
        'included', 'including', 'continue', 'continued',
        'continuing', 'set', 'setting', 'learn', 'learned',
        'learning', 'change', 'changed', 'changing', 'lead',
        'led', 'leading', 'understand', 'understood',
        'understanding', 'watch', 'watched', 'watching',
        'follow', 'followed', 'following', 'stop', 'stopped',
        'stopping', 'create', 'created', 'creating', 'speak',
        'spoke', 'spoken', 'speaking', 'allow', 'allowed',
        'allowing', 'add', 'added', 'adding', 'spend', 'spent',
        'spending', 'grow', 'grew', 'grown', 'growing', 'open',
        'opened', 'opening', 'walk', 'walked', 'walking',
        'win', 'won', 'winning', 'offer', 'offered', 'offering',
        'remember', 'remembered', 'remembering', 'love', 'loved',
        'loving', 'consider', 'considered', 'considering',
        'appear', 'appeared', 'appearing', 'buy', 'bought',
        'buying', 'wait', 'waited', 'waiting', 'serve',
        'served', 'serving', 'die', 'died', 'dying', 'send',
        'sent', 'sending', 'expect', 'expected', 'expecting',
        'build', 'built', 'building', 'stay', 'stayed',
        'staying', 'fall', 'fell', 'fallen', 'falling',
        'cut', 'cutting', 'reach', 'reached', 'reaching',
        'kill', 'killed', 'killing', 'remain', 'remained',
        'remaining', 'suggest', 'suggested', 'suggesting',
        'raise', 'raised', 'raising', 'pass', 'passed',
        'passing', 'sell', 'sold', 'selling', 'require',
        'required', 'requiring', 'report', 'reported',
        'reporting', 'decide', 'decided', 'deciding', 'pull',
        'pulled', 'pulling', 'one', 'two', 'three', 'first',
        'second', 'last', 'next', 'previous', 'other', 'another',
        'same', 'different', 'new', 'old', 'good', 'bad',
        'best', 'worst', 'better', 'worse', 'high', 'low',
        'big', 'small', 'large', 'little', 'long', 'short',
        'great', 'important', 'possible', 'sure', 'clear',
        'easy', 'hard', 'early', 'late', 'fast', 'slow',
        'right', 'wrong', 'left', 'full', 'empty', 'whole',
        'part', 'half', 'quarter', 'double', 'single',
        'multiple', 'various', 'several', 'many', 'much',
        'more', 'most', 'less', 'least', 'few', 'all',
        'none', 'some', 'any', 'each', 'every', 'both',
        'either', 'neither', 'such', 'only', 'own', 'just',
        'already', 'still', 'yet', 'ever', 'never',
        'always', 'often', 'sometimes', 'usually',
        'finally', 'eventually', 'actually', 'really',
        'probably', 'maybe', 'perhaps', 'certainly',
        'definitely', 'absolutely', 'completely',
        'totally', 'entirely', 'mostly', 'partly',
        'nearly', 'almost', 'quite', 'rather', 'pretty',
        'very', 'too', 'so', 'enough', 'well', 'badly',
        'hardly', 'barely', 'simply', 'easily', 'quickly',
        'slowly', 'carefully', 'properly', 'correctly',
        'directly', 'immediately', 'recently', 'finally',
        'suddenly', 'gradually', 'frequently', 'rarely',
        'issue', 'bug', 'fix', 'problem', 'error', 'crash',
        'fail', 'failure', 'broken', 'work', 'works',
        'working', 'broken', 'expected', 'actual',
        'behavior', 'behaviour', 'steps', 'reproduce',
        'reproduction', 'solution', 'workaround',
        'patch', 'proposal', 'suggested', 'see', 'look',
        'like', 'also', 'however', 'therefore', 'thus',
        'hence', 'moreover', 'furthermore', 'otherwise',
        'instead', 'meanwhile', 'besides', 'except',
        'despite', 'although', 'though', 'whereas',
        'while', 'because', 'since', 'unless', 'until',
        'whether', 'either', 'neither', 'both', 'all',
        'github', 'com', 'http', 'https', 'www', 'org',
        'openclaw', 'open', 'close', 'yes', 'no', 'ok',
        'okay', 'thanks', 'thank', 'please', 'sorry',
        'hello', 'hi', 'hey', 'bye', 'goodbye', 'welcome',
    }

    @staticmethod
    def parse(issue: Dict, repo_path: str = None) -> Dict:
        """解析单条Issue为结构化数据"""
        parsed = {
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "labels": issue.get("labels", []),
            "state": issue.get("state", ""),
            "created_at": issue.get("created_at", ""),
            "html_url": issue.get("html_url", ""),
            "error_snippets": [],
            "file_refs": [],
            "stack_trace_files": [],
            "is_bug": False,
            "error_type": None,
            "error_context": "",
            "line_refs": [],
            "expected_behavior": "",
            "actual_behavior": "",
            "steps_to_reproduce": [],
            "suggested_fix": "",
            "affected_functions": [],
            "language": None
        }

        # 判断是否为Bug类型
        bug_keywords = ["bug", "defect", "error", "fix", "crash", "fail", "broken",
                       "incorrect", "wrong", "unexpected", "exception", "regression",
                       "leak", "race condition", "deadlock", "infinite loop"]
        title_lower = parsed["title"].lower()
        labels_lower = [l.lower() for l in parsed["labels"]]
        parsed["is_bug"] = any(k in title_lower for k in bug_keywords) or \
                           any(k in labels_lower for k in bug_keywords)

        body = parsed["body"]

        # 提取代码/报错片段
        code_blocks = re.findall(r'```[\w]*\n(.*?)```', body, re.DOTALL)
        parsed["error_snippets"] = code_blocks

        # 区分堆栈跟踪和代码示例
        stack_trace_files = []
        code_example_files = []

        # 匹配文件路径引用（要求至少包含一个目录分隔符 /，避免匹配像 "Node.js" 这样的普通词汇）
        # 使用 + 而不是 * 来确保至少有一个 / 分隔符存在
        file_refs = re.findall(
            rf'([\w\-]+(?:/[\w\-]+)+\.{IssueParser.FILE_EXTENSIONS})',
            body
        )

        # 验证文件是否存在于仓库中（如果提供了repo_path）
        # 注意：只接受精确路径匹配，拒绝basename回退（避免选中无关文件）
        if repo_path and os.path.exists(repo_path):
            valid_refs = []
            for ref in file_refs:
                # 跳过明显无效的路径（如URL片段、绝对路径等）
                if ref.startswith('//') or ref.startswith('/'):
                    continue
                # 跳过GitHub URL相关路径（blob/tree/raw等）
                if 'github' in ref and 'com' in ref:
                    continue
                # 跳过用户附件路径
                if 'user-attachments' in ref or 'files/' in ref:
                    continue
                # 跳过包含 blob/ 或 tree/ 的路径（GitHub URL模式）
                if '/blob/' in ref or '/tree/' in ref:
                    continue
                # 跳过包含 .. 的路径（安全问题）
                if '..' in ref:
                    continue
                # 跳过以 com/ 开头的路径（通常是GitHub URL的域名部分）
                if ref.startswith('com/'):
                    continue

                full_path = os.path.join(repo_path, ref)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    valid_refs.append(ref)
                # 不再尝试递归basename匹配——那会引入大量无关文件
            file_refs = list(set(valid_refs))

        # 区分堆栈跟踪中的文件和代码示例中的文件
        for ref in file_refs:
            # 检查是否在堆栈跟踪上下文中
            if IssueParser._is_in_stack_trace(body, ref):
                stack_trace_files.append(ref)
            else:
                code_example_files.append(ref)

        # 如果堆栈跟踪中没有找到文件，但 file_refs 中有，
        # 且 body 包含堆栈跟踪特征，则将所有 file_refs 视为堆栈跟踪文件
        if not stack_trace_files and file_refs:
            if any(ind in body for ind in ['at ', 'File "', 'Traceback', 'stack', 'Error:', 'Exception:']):
                stack_trace_files = list(file_refs)

        parsed["file_refs"] = list(set(file_refs))
        parsed["stack_trace_files"] = list(set(stack_trace_files))

        # 检测项目语言
        parsed["language"] = IssueParser._detect_language(parsed["file_refs"])

        # 提取行号引用
        line_refs = re.findall(r'(?:line|L)\s*(\d+)', body, re.IGNORECASE)
        parsed["line_refs"] = [int(x) for x in line_refs]

        # 提取错误类型（扩展TS/JS错误）
        parsed["error_type"] = IssueParser.extract_error_type(body)

        # 提取错误上下文（错误消息和周围内容）
        parsed["error_context"] = IssueParser._extract_error_context(body)

        # 提取期望行为与实际行为
        parsed["expected_behavior"] = IssueParser._extract_section(body,
            ["expected", "expect", "should", "should be"])
        parsed["actual_behavior"] = IssueParser._extract_section(body,
            ["actual", "actually", "observed", "happens", "result"])

        # 提取复现步骤
        parsed["steps_to_reproduce"] = IssueParser._extract_steps(body)

        # 提取建议修复
        parsed["suggested_fix"] = IssueParser._extract_suggested_fix(body)

        # 提取受影响的函数名（改进过滤）
        parsed["affected_functions"] = IssueParser._extract_functions(body)

        logger.debug(f"Parsed issue #{parsed['number']}: is_bug={parsed['is_bug']}, "
                     f"lang={parsed['language']}, files={parsed['file_refs']}, "
                     f"stack_files={parsed['stack_trace_files']}, "
                     f"error={parsed['error_type']}")
        return parsed

    @staticmethod
    def _is_in_stack_trace(body: str, file_ref: str) -> bool:
        """检查文件引用是否出现在堆栈跟踪上下文中"""
        lines = body.split('\n')
        for i, line in enumerate(lines):
            if file_ref in line:
                # 检查前后行是否有堆栈跟踪特征
                context = []
                if i > 0:
                    context.append(lines[i-1])
                context.append(line)
                if i < len(lines) - 1:
                    context.append(lines[i+1])

                context_str = '\n'.join(context)
                stack_indicators = [
                    'at ', 'File "', 'line ', 'Error:', 'Exception:',
                    'Traceback', 'stack', 'call stack', ' Stack:',
                    'in <', 'in (', '->', '=>'
                ]
                if any(ind in context_str for ind in stack_indicators):
                    return True
        return False

    @staticmethod
    def _detect_language(file_refs: List[str]) -> Optional[str]:
        """根据文件引用检测项目语言"""
        if not file_refs:
            return None
        ext_counts = {}
        for ref in file_refs:
            ext = ref.split('.')[-1].lower() if '.' in ref else ''
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        if not ext_counts:
            return None

        lang_map = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'jsx': 'javascript', 'tsx': 'typescript', 'java': 'java',
            'go': 'go', 'rs': 'rust', 'cpp': 'cpp', 'c': 'c',
            'h': 'c', 'hpp': 'cpp', 'cs': 'csharp', 'rb': 'ruby',
            'php': 'php', 'swift': 'swift', 'kt': 'kotlin',
            'scala': 'scala', 'r': 'r', 'm': 'objc', 'mm': 'objc'
        }

        # 优先排除歧义扩展名（如 .m 可能是 MATLAB/ObjC/Mathematica）
        # 如果存在非歧义扩展名，优先使用非歧义扩展名
        ambiguous_exts = {'m', 'pl', 'v'}
        non_ambiguous = {k: v for k, v in ext_counts.items() if k not in ambiguous_exts}
        if non_ambiguous:
            main_ext = max(non_ambiguous, key=non_ambiguous.get)
        else:
            main_ext = max(ext_counts, key=ext_counts.get)

        return lang_map.get(main_ext, main_ext)

    @staticmethod
    def _extract_section(body: str, headers: List[str]) -> str:
        """提取特定章节内容"""
        lines = body.split('\n')
        result = []
        capturing = False
        for line in lines:
            line_lower = line.lower().strip()
            # 检查是否是目标章节头
            if any(line_lower.startswith(h + ':') or line_lower.startswith('**' + h + '**')
                   or line_lower.startswith('### ' + h) for h in headers):
                capturing = True
                continue
            # 遇到下一个章节头停止
            if capturing and (line.startswith('#') or line.startswith('**')
                              or re.match(r'^[A-Z][a-z]+:', line)):
                break
            if capturing:
                result.append(line)
        return '\n'.join(result).strip()

    @staticmethod
    def _extract_steps(body: str) -> List[str]:
        """提取复现步骤"""
        steps = []
        lines = body.split('\n')
        in_steps = False
        for line in lines:
            line_lower = line.lower().strip()
            if 'step' in line_lower or 'reproduce' in line_lower or 'reproduction' in line_lower:
                in_steps = True
                continue
            if in_steps:
                # 匹配数字列表或bullet
                if re.match(r'^(\d+[.\)]\s+|[-*]\s+)', line.strip()):
                    steps.append(line.strip())
                elif line.strip() == '':
                    continue
                elif line.startswith('#'):
                    break
        return steps

    @staticmethod
    def _extract_suggested_fix(body: str) -> str:
        """提取建议修复方案"""
        fix_keywords = ["fix:", "solution:", "workaround:", "patch:", "proposal:", "suggested fix"]
        lines = body.split('\n')
        result = []
        capturing = False
        for line in lines:
            line_lower = line.lower().strip()
            if any(line_lower.startswith(k) for k in fix_keywords):
                capturing = True
                continue
            if capturing:
                if line.startswith('#') or (line.strip() and line.strip()[0].isupper() and ':' in line[:30]):
                    break
                result.append(line)
        return '\n'.join(result).strip()

    @staticmethod
    def _extract_functions(body: str) -> List[str]:
        """从正文中提取函数/方法名引用（改进过滤）"""
        # 使用列表保持顺序（先出现的更可能是关键函数），并用set去重
        functions = []
        seen = set()

        # 模式1：backtick引用 `functionName` — 最高置信度
        for match in re.findall(r'`([\w_]+)`', body):
            if IssueParser._is_valid_function_name(match, seen):
                functions.append(match)
                seen.add(match.lower())

        # 模式2：functionName( — 中等置信度
        for match in re.findall(r'\b([\w_]+)\s*\(', body):
            if IssueParser._is_valid_function_name(match, seen):
                functions.append(match)
                seen.add(match.lower())

        # 模式3：className.methodName( — 中等置信度
        for match in re.findall(r'\b([\w_]+)\.\w+\s*\(', body):
            if IssueParser._is_valid_function_name(match, seen):
                functions.append(match)
                seen.add(match.lower())

        return functions

    @staticmethod
    def _is_valid_function_name(match: str, seen: set) -> bool:
        """检查提取的函数名是否有效"""
        if len(match) <= 1:
            return False
        match_lower = match.lower()
        if match_lower in seen:
            return False
        # 过滤常见非函数词
        if match_lower in IssueParser.COMMON_WORDS:
            return False
        # 排除纯数字、版本号
        if re.match(r'^\d+$', match):
            return False
        if re.match(r'^\d+[._]\d+', match):
            return False
        # 排除平台名、架构名
        if match_lower in {'amd64', 'x86', 'arm64', 'darwin', 'linux', 'windows',
                           'ios', 'android', 'macos', 'ubuntu', 'debian', 'freebsd'}:
            return False
        # 排除常见英文单词（4字母以上且全小写的，更可能是普通单词而非函数名）
        if len(match) >= 4 and match.isalpha() and match.islower():
            # 允许一些明显是动词/动作的词，但排除常见名词/形容词
            common_nouns = {
                'usage', 'regression', 'behavior', 'behaviour', 'example', 'sample',
                'result', 'output', 'input', 'value', 'default', 'custom', 'local',
                'global', 'public', 'private', 'static', 'final', 'abstract',
                'internal', 'external', 'manual', 'automatic', 'random', 'specific',
                'general', 'normal', 'standard', 'common', 'single', 'double',
                'multiple', 'various', 'several', 'certain', 'particular',
                'previous', 'following', 'current', 'recent', 'future',
                'potential', 'possible', 'actual', 'expected', 'unexpected',
                'correct', 'incorrect', 'proper', 'appropriate', 'suitable',
                'available', 'accessible', 'visible', 'hidden', 'missing',
                'invalid', 'valid', 'empty', 'full', 'complete', 'partial',
                'total', 'absolute', 'relative', 'positive', 'negative',
                'minimum', 'maximum', 'average', 'summary', 'detail',
                'description', 'information', 'documentation', 'reference',
                'version', 'release', 'build', 'deploy', 'install',
                'configuration', 'environment', 'production', 'development',
                'application', 'program', 'project', 'package', 'module',
                'component', 'element', 'item', 'entry', 'record', 'instance',
                'object', 'subject', 'context', 'content', 'text', 'string',
                'number', 'integer', 'boolean', 'array', 'list', 'map',
                'queue', 'stack', 'tree', 'graph', 'table', 'view',
                'model', 'controller', 'service', 'helper', 'manager',
                'handler', 'listener', 'observer', 'publisher', 'subscriber',
                'provider', 'consumer', 'producer', 'worker', 'runner',
                'driver', 'adapter', 'connector', 'wrapper', 'proxy',
                'factory', 'builder', 'parser', 'serializer', 'converter',
                'encoder', 'decoder', 'compressor', 'extractor', 'generator',
                'validator', 'checker', 'tester', 'monitor', 'tracker',
                'logger', 'reporter', 'analyzer', 'processor', 'executor',
                'scheduler', 'dispatcher', 'router', 'controller', 'broker',
                'channel', 'session', 'connection', 'request', 'response',
                'message', 'event', 'signal', 'trigger', 'action',
                'operation', 'transaction', 'procedure', 'function',
                'method', 'routine', 'task', 'job', 'process', 'thread',
                'issue', 'problem', 'error', 'warning', 'notice', 'info',
                'debug', 'trace', 'fatal', 'critical', 'severe', 'major',
                'minor', 'trivial', 'blocker', 'urgent', 'high', 'medium',
                'low', 'priority', 'severity', 'impact', 'scope', 'range',
                'limit', 'boundary', 'threshold', 'constraint', 'restriction',
                'requirement', 'condition', 'assumption', 'expectation',
                'scenario', 'case', 'situation', 'state', 'status', 'mode',
                'phase', 'stage', 'step', 'action', 'operation', 'process',
                'workflow', 'pipeline', 'sequence', 'order', 'sort',
                'group', 'category', 'class', 'type', 'kind', 'sort',
                'format', 'style', 'pattern', 'template', 'schema',
                'structure', 'layout', 'design', 'architecture', 'framework',
                'platform', 'system', 'engine', 'core', 'base', 'foundation',
                'source', 'origin', 'target', 'destination', 'goal',
                'purpose', 'reason', 'cause', 'effect', 'result',
                'consequence', 'outcome', 'solution', 'answer', 'response',
                'reply', 'feedback', 'comment', 'note', 'remark',
                'suggestion', 'recommendation', 'advice', 'tip', 'hint',
                'clue', 'evidence', 'proof', 'verification', 'validation',
                'confirmation', 'approval', 'acceptance', 'rejection',
                'exception', 'omission', 'inclusion', 'exclusion',
                'addition', 'deletion', 'modification', 'change', 'update',
                'upgrade', 'downgrade', 'migration', 'transition',
                'transformation', 'conversion', 'adaptation', 'adjustment',
                'correction', 'fix', 'repair', 'patch', 'workaround',
                'alternative', 'option', 'choice', 'selection', 'preference',
                'setting', 'configuration', 'parameter', 'argument',
                'variable', 'constant', 'literal', 'expression',
                'statement', 'declaration', 'definition', 'assignment',
                'initialization', 'implementation', 'override', 'overload',
            }
            if match_lower in common_nouns:
                return False
        return True

    @staticmethod
    def _extract_error_context(body: str) -> str:
        """提取错误上下文（错误消息和周围内容）"""
        # 查找错误消息模式
        error_patterns = [
            r'(Error[\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
            r'(Exception[\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
            r'(Traceback \(most recent call last\):.*?)(?:\n\n|\n[A-Z]|$)',
            r'(TypeError[\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
            r'(ReferenceError[\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
            r'(Cannot [\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
            r'(Failed to [\s\w]*:.*?)(?:\n\n|\n[A-Z]|$)',
        ]

        for pattern in error_patterns:
            match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
            if match:
                context = match.group(1).strip()
                # 限制长度
                if len(context) > 500:
                    context = context[:500] + "..."
                return context

        return ""

    @staticmethod
    def filter_bug_issues(issues: List[Dict], repo_path: str = None) -> List[Dict]:
        """过滤出Bug类型Issue"""
        bug_issues = []
        for issue in issues:
            parsed = IssueParser.parse(issue, repo_path)
            if parsed["is_bug"]:
                bug_issues.append(parsed)
        logger.info(f"Filtered {len(bug_issues)} bug issues from {len(issues)} total")
        return bug_issues

    @staticmethod
    def extract_error_type(body: str) -> Optional[str]:
        """从Issue正文中提取错误类型（支持多语言）"""
        error_patterns = [
            # Python
            r'(IndentationError)', r'(NameError)', r'(SyntaxError)',
            r'(IndexError)', r'(TypeError)', r'(KeyError)',
            r'(AttributeError)', r'(ZeroDivisionError)',
            r'(ImportError)', r'(ModuleNotFoundError)',
            r'(ValueError)', r'(RuntimeError)', r'(AssertionError)',
            # JavaScript/TypeScript
            r'(ReferenceError)', r'(TypeError)', r'(SyntaxError)',
            r'(RangeError)', r'(EvalError)', r'(URIError)',
            r'(Cannot read property)', r'(is not a function)',
            r'(undefined is not)', r'(Cannot find module)',
            r'(Cannot find name)', r'(Property does not exist)',
            r'(TS\d+)',  # TypeScript compiler errors
            # Java
            r'(NullPointerException)', r'(ArrayIndexOutOfBoundsException)',
            r'(ClassNotFoundException)', r'(IllegalArgumentException)',
            # Go
            r'(panic:)', r'(runtime error:)',
            # Rust
            r'(borrow checker)', r'(lifetime error)',
            # General
            r'(Segmentation fault)', r'(stack overflow)',
            r'(memory leak)', r'(deadlock)',
        ]
        for pattern in error_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
