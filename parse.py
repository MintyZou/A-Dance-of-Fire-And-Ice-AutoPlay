"""读取 .adofai 关卡文件。

ADOFAI 编辑器导出的 .adofai 名义上是 JSON，但实际文件常带有：
  * UTF-8 BOM
  * 尾随逗号，甚至连续空逗号 ``, ,``（编辑器 bug）
  * 字符串内未转义的换行 / 制表符
  * ``//`` 或 ``/* */`` 注释
标准库 json 无法直接解析，因此先做一次容错清洗。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 旧版 pathData 单字符 -> 绝对角度
PATH_CHARS = {
    "R": 0, "p": 15, "J": 30, "E": 45, "T": 60, "o": 75, "U": 90,
    "q": 105, "G": 120, "Q": 135, "H": 150, "W": 165, "L": 180,
    "x": 195, "N": 210, "Z": 225, "F": 240, "V": 255, "D": 270,
    "Y": 285, "B": 300, "C": 315, "M": 330, "A": 345, "!": 999,
}


def _needs_comma(out: list[str]) -> bool:
    """字符串外的 token 起点：判断编辑器是否漏写了逗号。

    编辑器偶尔在 ``]`` / ``}`` / 值 之后直接接下一个键或元素而不写逗号，
    既可能是 ``] "decorations":``，也可能是 ``} { "floor": ...``。
    尾部为「值结束符」时说明缺逗号；``{`` / ``[`` / ``:`` / ``,`` 之后
    是合法位置，不补。
    """
    j = len(out) - 1
    while j >= 0 and out[j].isspace():
        j -= 1
    return j >= 0 and (out[j] in ']}"' or out[j].isdigit()
                       or out[j] in 'eEl.')


def sanitize_json(text: str) -> str:
    """把非标准 JSON 清洗成 json.loads 可接受的形式。

    用状态机区分「字符串内 / 字符串外」，避免误伤字符串里的
    ``http://``、``//`` 之类内容。
    """
    if text and text[0] == "\ufeff":
        text = text[1:]
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":                      # 已有的转义序列原样保留
                out.append(c)
                i += 1
                if i < n:
                    out.append(text[i])
                    i += 1
                continue
            if c == '"':
                in_string = False
                out.append(c)
            elif c == "\n":
                out.append("\\n")              # 裸换行 -> 转义
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(c)
            i += 1
            continue

        if c in '"{[' and _needs_comma(out):
            out.append(',')
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1

    s = "".join(out)
    s = re.sub(r"(,\s*){2,}", ", ", s)      # 折叠 ", ," 之类的空元素
    s = re.sub(r",\s*([}\]])", r"\1", s)    # 去掉 } ] 前的尾逗号
    return s


def load(path: str | Path) -> dict:
    """读取并解析 .adofai，返回原始 dict。"""
    data = json.loads(sanitize_json(Path(path).read_bytes().decode("utf-8-sig")))
    if "settings" not in data or "actions" not in data:
        raise ValueError("不是有效的 .adofai 文件：缺少 settings / actions")
    if "angleData" not in data and "pathData" not in data:
        raise ValueError("不是有效的 .adofai 文件：缺少 angleData / pathData")
    return data


def path_to_angles(path_data: str) -> list[float]:
    """把旧版 pathData 字符串转成 angleData。"""
    angles = [float(PATH_CHARS[c]) for c in path_data if c in PATH_CHARS]
    if not angles:
        return [0.0]
    # 末尾补一个方向，使最后一块砖也能算出相对角度（与编辑器/社区库一致）
    angles.append(angles[-1] if angles[-1] != 999 else (angles[-2] + 180) % 360)
    return angles


def get_angle_data(data: dict) -> list[float]:
    """取 angleData；老格式则从 pathData 转换。"""
    if "angleData" in data:
        return [float(x) for x in data["angleData"]]
    return path_to_angles(data["pathData"])
