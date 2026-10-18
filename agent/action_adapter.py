"""Translate observed native action markup without executing model-supplied code."""
import ast
import inspect
import re
from typing import get_args


_NAME = r'[A-Za-z_][A-Za-z_0-9]*'
_BLOCK = re.compile(r'<(tool_call|function_calls)>(.*?)</\1>', re.DOTALL)


def to_action_call(text: str) -> str | None:
    # Imported lazily so the public parser and adapter share the same error type.
    from agent.agent import ActionParseError, FORMAT_REMINDER
    from agent.env import ALLOWED_ACTIONS, report_verification
    from browsergym.core.action import functions

    if re.search(r'```action(?=\s|$)', text):
        return None
    openings = re.findall(r'<(?:tool_call|function_calls)>', text)
    if len(openings) > 1:
        raise ActionParseError('Reply with exactly one action per turn.')
    blocks = list(_BLOCK.finditer(text))
    if not blocks:
        return None
    kind, body = blocks[0].groups()
    if kind == 'tool_call':
        function = re.fullmatch(r'\s*<function=(' + _NAME + r')>(.*?)</function>\s*', body, re.DOTALL)
        parameter = re.compile(r'<parameter=(' + _NAME + r')>(.*?)</parameter>', re.DOTALL)
    else:
        if len(re.findall(r'<invoke\b', body)) > 1:
            raise ActionParseError('Reply with exactly one action per turn.')
        function = re.fullmatch(r'\s*<invoke name="(' + _NAME + r')">(.*?)</invoke>\s*', body, re.DOTALL)
        parameter = re.compile(r'<parameter name="(' + _NAME + r')">(.*?)</parameter>', re.DOTALL)
    if function is None:
        raise ActionParseError(FORMAT_REMINDER)
    name, body = function.groups()
    if kind == 'tool_call' and name == 'action':
        # Observed MiMo wrapper. The caller's regular AST/signature validation
        # still enforces one allowed call and literal arguments.
        return body.strip()
    if name not in ALLOWED_ACTIONS | {'report_verification'}:
        raise ActionParseError(FORMAT_REMINDER)
    signature = inspect.signature(report_verification if name == 'report_verification' else getattr(functions, name))
    kwargs = {}
    cursor = 0
    try:
        for match in parameter.finditer(body):
            if body[cursor:match.start()].strip():
                raise ValueError
            key, value = match.groups()
            if key in kwargs or key not in signature.parameters:
                raise ValueError
            value = value.strip()
            annotation = signature.parameters[key].annotation
            if annotation is str or str in get_args(annotation):
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    value = value[1:-1]
            else:
                value = ast.literal_eval(value)
            kwargs[key] = value
            cursor = match.end()
        if body[cursor:].strip():
            raise ValueError
        signature.bind(**kwargs)
    except (ValueError, SyntaxError, TypeError):
        raise ActionParseError(FORMAT_REMINDER) from None
    return f"{name}({', '.join(f'{key}={value!r}' for key, value in kwargs.items())})"
