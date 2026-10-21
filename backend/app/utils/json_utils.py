# AIMETA P=JSON工具_JSON解析和修复|R=安全解析_格式修复|NR=不含业务逻辑|E=parse_json_safely|X=internal|A=工具函数|D=json|S=none|RD=./README.ai
import json
import re


def remove_think_tags(raw_text: str) -> str:
    """移除 <think></think> 标签，避免污染结果。"""
    if not raw_text:
        return raw_text
    return re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()


def unwrap_markdown_json(raw_text: str) -> str:
    """从 Markdown 或普通文本中提取 JSON 字符串。"""
    if not raw_text:
        return raw_text

    trimmed = raw_text.strip()

    fence_match = re.search(r"```(?:json|JSON)?\s*(.*?)\s*```", trimmed, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            return candidate

    json_start_candidates = [idx for idx in (trimmed.find("{"), trimmed.find("[")) if idx != -1]
    if json_start_candidates:
        start_idx = min(json_start_candidates)
        closing_brace = trimmed.rfind("}")
        closing_bracket = trimmed.rfind("]")
        end_idx = max(closing_brace, closing_bracket)
        if end_idx != -1 and end_idx > start_idx:
            candidate = trimmed[start_idx : end_idx + 1].strip()
            if candidate:
                return candidate

    return trimmed


def sanitize_json_like_text(raw_text: str) -> str:
    """对可能含有未转义换行/引号的 JSON 文本进行清洗。"""
    if not raw_text:
        return raw_text

    result = []
    in_string = False
    escape_next = False
    length = len(raw_text)
    i = 0
    while i < length:
        ch = raw_text[i]
        if in_string:
            if escape_next:
                result.append(ch)
                escape_next = False
            elif ch == "\\":
                result.append(ch)
                escape_next = True
            elif ch == '"':
                j = i + 1
                while j < length and raw_text[j] in " \t\r\n":
                    j += 1

                if j >= length or raw_text[j] in "}]":
                    in_string = False
                    result.append(ch)
                elif raw_text[j] in ",:":
                    in_string = False
                    result.append(ch)
                else:
                    result.extend(["\\", '"'])
            elif ch == "\n":
                result.extend(["\\", "n"])
            elif ch == "\r":
                result.extend(["\\", "r"])
            elif ch == "\t":
                result.extend(["\\", "t"])
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)
        i += 1

    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    result = []
    quoted = escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            following = index + 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following < len(text) and text[following] in "}]":
                continue
        result.append(char)
    return "".join(result)


def _close_json(text: str) -> str:
    stack = []
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or stack.pop() != char:
                return text
    if quoted:
        if escaped:
            text = text[:-1]
        text += '"'
    return _strip_trailing_commas(text + "".join(reversed(stack)))


def looks_like_json(raw_text: str) -> bool:
    text = remove_think_tags(raw_text or "").lstrip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    return text.startswith(("{", "[", '"')) or bool(re.search(r'[\[{]\s*["{\[]', text))


def parse_llm_json(raw_text: str, *, allow_lossy: bool = False):
    """默认只修复包裹/转义/尾逗号；截断修复必须由非落库调用显式选择。"""
    text = remove_think_tags(raw_text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 只移除结构外的 Markdown 包裹，保留正文字符串里的 ```。
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    fence = re.search(r"```(?:json)?\s*", text, re.IGNORECASE)
    if fence and (not starts or fence.start() < min(starts)):
        text = text[fence.end():].strip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    candidate = text[min(starts):] if starts else text
    for value in (candidate, sanitize_json_like_text(candidate)):
        cleaned = _strip_trailing_commas(value)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 完整 JSON 后的解释文字不属于结构内容。
            try:
                result, end = json.JSONDecoder().raw_decode(cleaned)
                suffix = cleaned[end:].lstrip()
                if not suffix or suffix[0] not in '{[}]",:':
                    return result
            except json.JSONDecodeError:
                pass
    if not allow_lossy:
        raise json.JSONDecodeError("AI 输出不完整或格式不正确，请重试", text, 0)
    for value in (candidate, sanitize_json_like_text(candidate)):
        try:
            return json.loads(_close_json(_strip_trailing_commas(value)))
        except json.JSONDecodeError:
            pass
    # 无法补齐不完整的值时，退回最后一个完整元素；不在字符串内部截断。
    quoted = escaped = False
    boundaries = []
    for index, char in enumerate(candidate):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            boundaries.append(index)
    for index in reversed(boundaries[-256:]):
        try:
            return json.loads(_close_json(_strip_trailing_commas(candidate[:index])))
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("AI 返回的内容不是可解析的 JSON", text, 0)
