import traceback
import sys
from functools import wraps
from typing import Callable, Any

from src.common_utils.log_manager import get_logger


logger = get_logger()


class HarnessException(Exception):
    """Harness框架基础异常"""
    pass


class GitHubAPIException(HarnessException):
    """GitHub API调用异常"""
    pass


class GitOperationException(HarnessException):
    """Git操作异常"""
    pass


class RepairException(HarnessException):
    """代码修复异常"""
    pass


class SpecCheckException(HarnessException):
    """规范校验异常"""
    pass


class ConfigException(HarnessException):
    """配置异常"""
    pass


def safe_call(func: Callable) -> Callable:
    """装饰器：捕获异常，记录日志，不抛出"""
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Exception in {func.__name__}: {e}")
            logger.debug(traceback.format_exc())
            return None
    return wrapper


def safe_call_with_default(default: Any):
    """装饰器：捕获异常，返回默认值"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Exception in {func.__name__}: {e}")
                logger.debug(traceback.format_exc())
                return default
        return wrapper
    return decorator


def log_uncaught_exceptions(exc_type, exc_value, exc_traceback):
    """全局未捕获异常处理"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.error(f"Uncaught exception:\n{msg}")


sys.excepthook = log_uncaught_exceptions
