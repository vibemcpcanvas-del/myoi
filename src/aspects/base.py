"""src/aspects/base.py
Base abstractions and utilities for Aspect-Oriented Programming (AOP).
Supports seamless wrapping of both synchronous and asynchronous functions.
"""

from dataclasses import dataclass, field
import functools
import inspect
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, TypeVar, Union

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class JoinPoint:
    """Represents the execution context at an advice join point."""

    target: Callable[..., Any]
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def proceed(self) -> Any:
        """Executes the synchronous target."""
        return self.target(*self.args, **self.kwargs)

    async def proceed_async(self) -> Any:
        """Executes the asynchronous target."""
        result = self.target(*self.args, **self.kwargs)
        if inspect.iscoroutine(result):
            return await result
        return result


def is_coroutine_callable(func: Callable[..., Any]) -> bool:
    """Detects whether a callable returns a coroutine."""
    if inspect.iscoroutinefunction(func):
        return True
    if hasattr(func, "__wrapped__"):
        return is_coroutine_callable(func.__wrapped__)
    if hasattr(func, "__call__") and not inspect.isfunction(func):
        return inspect.iscoroutinefunction(func.__call__)
    return False
