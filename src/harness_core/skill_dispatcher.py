import os
import sys
import importlib.util
from typing import Any, Dict, Optional

from src.common_utils.log_manager import get_logger

logger = get_logger()


class SkillDispatcher:
    """技能调度分发器：根据技能名称，动态加载对应skill包run.py并调用执行"""

    def __init__(self):
        self._skill_modules: Dict[str, Any] = {}
        # 使用相对于项目根目录的路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        self.SKILLS_DIR = os.path.join(project_root, ".harness", "skills")

    def _load_skill_module(self, skill_name: str) -> Optional[Any]:
        """导入技能脚本"""
        if skill_name in self._skill_modules:
            return self._skill_modules[skill_name]

        skill_path = os.path.join(self.SKILLS_DIR, skill_name, "run.py")
        if not os.path.exists(skill_path):
            logger.error(f"Skill not found: {skill_path}")
            return None

        try:
            spec = importlib.util.spec_from_file_location(skill_name, skill_path)
            if spec is None or spec.loader is None:
                logger.error(f"Cannot load spec for skill: {skill_name}")
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[skill_name] = module
            spec.loader.exec_module(module)
            self._skill_modules[skill_name] = module
            logger.info(f"Skill module loaded: {skill_name}")
            return module
        except Exception as e:
            logger.error(f"Failed to load skill {skill_name}: {e}")
            return None

    def run_skill_task(self, skill_name: str, **kwargs) -> Dict[str, Any]:
        """传入参数执行技能逻辑"""
        logger.info(f"Dispatching skill: {skill_name}")
        module = self._load_skill_module(skill_name)
        if module is None:
            return {"success": False, "error": f"Skill {skill_name} not found or failed to load"}

        try:
            if hasattr(module, "run"):
                result = module.run(**kwargs)
                if result is None:
                    result = {}
                if not isinstance(result, dict):
                    result = {"success": True, "data": result}
                logger.info(f"Skill {skill_name} executed, success={result.get('success', True)}")
                return result
            else:
                logger.error(f"Skill {skill_name} has no run() function")
                return {"success": False, "error": f"Skill {skill_name} has no run() function"}
        except Exception as e:
            logger.error(f"Skill {skill_name} execution failed: {e}")
            return {"success": False, "error": str(e)}
