import os
import glob
from typing import Dict, List, Optional

from src.common_utils.log_manager import get_logger
from src.common_utils.file_helper import FileHelper

logger = get_logger()


class WikiContextReader:
    """知识库读取器：读取Wiki问题库、模板、结构文档，提供问题匹配、方案调取接口"""

    def __init__(self):
        self._wiki_cache: Dict[str, str] = {}
        self._loaded = False
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.WIKI_DIR = os.path.join(project_root, ".harness", "wiki")

    def load_all(self) -> Dict[str, str]:
        if self._loaded:
            return self._wiki_cache

        if not os.path.exists(self.WIKI_DIR):
            logger.error(f"Wiki directory not found: {self.WIKI_DIR}")
            return self._wiki_cache

        for filepath in glob.glob(os.path.join(self.WIKI_DIR, "*.md")):
            name = os.path.basename(filepath)
            try:
                self._wiki_cache[name] = FileHelper.read_file(filepath)
                logger.info(f"Loaded wiki: {name}")
            except Exception as e:
                logger.error(f"Failed to load wiki {name}: {e}")

        self._loaded = True
        logger.info(f"Total wiki files loaded: {len(self._wiki_cache)}")
        return self._wiki_cache

    def get_wiki(self, name: str) -> Optional[str]:
        if not self._loaded:
            self.load_all()
        return self._wiki_cache.get(name)

    def match_error_category(self, error_text: str) -> Optional[str]:
        """根据错误文本匹配问题类别"""
        error_lower = error_text.lower()
        wiki = self.get_wiki("error_match_rule.md")
        if not wiki:
            return None

        # 简单关键词匹配
        keywords_map = {
            "indentationerror": "缩进错误",
            "nameerror": "变量未定义",
            "syntaxerror": "语法错误",
            "indexerror": "索引越界",
            "typeerror": "类型错误",
            "keyerror": "键不存在",
            "attributeerror": "属性错误",
            "zerodivisionerror": "除零错误",
            "importerror": "导入错误",
            "modulenotfounderror": "导入错误",
            "flake8": "规范问题",
            "pylint": "规范问题",
            "pep8": "规范问题",
        }

        for keyword, category in keywords_map.items():
            if keyword in error_lower:
                return category
        return None

    def get_repair_strategy(self, category: str) -> Dict[str, str]:
        """获取修复策略"""
        wiki = self.get_wiki("common_bug_repair_lib.md")
        strategy = {
            "category": category,
            "strategy": "autopep8 自动格式化",
            "check": "python -m py_compile 通过"
        }
        # 可扩展更精细的匹配
        return strategy

    def get_commit_template(self) -> str:
        """获取提交模板"""
        template = self.get_wiki("commit_message_template.md")
        if template:
            return template
        return "[Fix] #{issue_number} {description}"
