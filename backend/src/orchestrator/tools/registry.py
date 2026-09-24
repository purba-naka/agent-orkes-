from collections.abc import Awaitable, Callable
import inspect
from typing import Any

CodeTool = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class CodeToolRegistry:
    def __init__(self) -> None:
        self._implementations: dict[tuple[str, str], CodeTool] = {}

    def register(
        self,
        implementation_key: str,
        implementation_version: str,
        factory: CodeTool,
    ) -> None:
        identity = (implementation_key, implementation_version)
        if identity in self._implementations:
            raise ValueError(
                f"Code tool {implementation_key}@{implementation_version} is already registered"
            )
        self._implementations[identity] = factory

    def contains(self, implementation_key: str, implementation_version: str) -> bool:
        return (implementation_key, implementation_version) in self._implementations

    def resolve(self, implementation_key: str, implementation_version: str) -> CodeTool:
        try:
            return self._implementations[(implementation_key, implementation_version)]
        except KeyError as exc:
            raise KeyError(
                f"Code tool {implementation_key}@{implementation_version} is not registered"
            ) from exc

    async def invoke(
        self,
        implementation_key: str,
        implementation_version: str,
        input_data: dict[str, Any],
    ) -> Any:
        result = self.resolve(implementation_key, implementation_version)(input_data)
        return await result if inspect.isawaitable(result) else result


code_tool_registry = CodeToolRegistry()
