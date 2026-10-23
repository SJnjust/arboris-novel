REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


def optional_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("配置必须为文本")
    return value.strip() or None


def reasoning_effort(value):
    value = optional_text(value)
    if value is not None:
        value = value.lower()
        if value not in REASONING_EFFORTS:
            raise ValueError("推理强度须为 none、minimal、low、medium、high 或 xhigh")
    return value
