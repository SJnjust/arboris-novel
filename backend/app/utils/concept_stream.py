"""仅抽取根对象 ai_message 字符串，不向 SSE 泄露其余 JSON 字段。"""
import json

from .json_utils import remove_think_tags


class MessageDeltas:
    def __init__(self):
        self.prefix = ""
        self.started = False
        self.depth = 0
        self.quoted = False
        self.escaped = False
        self.key = None
        self.token = []
        self.kind = None
        self.expect_value = False
        self.escape = ""
        self.high = None
        self.text = ""

    def _character(self, char):
        if self.escape:
            self.escape += char
            if self.escape == "\\u" or self.escape.startswith("\\u") and len(self.escape) < 6:
                return ""
            char = json.loads('"' + self.escape + '"')
            self.escape = ""
        elif char == "\\":
            self.escape = char
            return ""
        code = ord(char)
        if 0xD800 <= code <= 0xDBFF:
            self.high = code
            return ""
        if self.high is not None:
            if not 0xDC00 <= code <= 0xDFFF:
                raise ValueError("不完整的 Unicode 字符")
            char = chr(0x10000 + (self.high - 0xD800) * 1024 + code - 0xDC00)
            self.high = None
        elif 0xDC00 <= code <= 0xDFFF:
            raise ValueError("不完整的 Unicode 字符")
        return char

    def feed(self, chunk):
        if not self.started:
            self.prefix += chunk
            visible = remove_think_tags(self.prefix)
            if "<think>" in visible:
                visible = visible.split("<think>", 1)[0]
            start = visible.find("{")
            if start < 0:
                return ""
            chunk = visible[start:]
            self.started = True
            self.prefix = ""
        result = []
        for char in chunk:
            if self.quoted:
                if char == '"' and not self.escaped:
                    self.quoted = False
                    if self.kind == "key":
                        self.key = json.loads('"' + "".join(self.token) + '"')
                    self.kind = None
                else:
                    if self.kind == "message":
                        result.append(self._character(char))
                    elif self.kind == "key":
                        self.token.append(char)
                    if self.escaped:
                        self.escaped = False
                    elif char == "\\":
                        self.escaped = True
            elif char == '"':
                self.quoted = True
                self.token = []
                self.kind = "message" if self.depth == 1 and self.expect_value and self.key == "ai_message" else (
                    "key" if self.depth == 1 and not self.expect_value else None)
            elif char in "{[":
                self.depth += 1
            elif char in "}]":
                self.depth -= 1
            elif char == ":" and self.depth == 1:
                self.expect_value = True
            elif char == "," and self.depth == 1:
                self.key = None
                self.expect_value = False
        delta = "".join(result)
        self.text += delta
        return delta
