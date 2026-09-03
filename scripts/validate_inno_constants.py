"""Reject unknown Inno Setup constants before they can fail at runtime."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Inno Setup 6 constants documented at https://jrsoftware.org/ishelp/topic_consts.htm.
# Deprecated aliases are included because the compiler still accepts them; new code
# should prefer the current/auto spellings from the same page.
SIMPLE_CONSTANTS = frozenset(
    [
        "app",
        "win",
        "sys",
        "sysnative",
        "syswow64",
        "src",
        "sd",
        "commonpf",
        "commonpf32",
        "commonpf64",
        "commoncf",
        "commoncf32",
        "commoncf64",
        "tmp",
        "commonfonts",
        "dao",
        "dotnet11",
        "dotnet20",
        "dotnet2032",
        "dotnet2064",
        "dotnet40",
        "dotnet4032",
        "dotnet4064",
        "group",
        "localappdata",
        "userappdata",
        "commonappdata",
        "usercf",
        "userdesktop",
        "commondesktop",
        "userdocs",
        "commondocs",
        "userfavorites",
        "userfonts",
        "userpf",
        "userprograms",
        "commonprograms",
        "usersavedgames",
        "usersendto",
        "userstartmenu",
        "commonstartmenu",
        "userstartup",
        "commonstartup",
        "usertemplates",
        "commontemplates",
        "autoappdata",
        "autocf",
        "autocf32",
        "autocf64",
        "autodesktop",
        "autodocs",
        "autofonts",
        "autopf",
        "autopf32",
        "autopf64",
        "autoprograms",
        "autostartmenu",
        "autostartup",
        "autotemplates",
        "cf",
        "cf32",
        "cf64",
        "fonts",
        "pf",
        "pf32",
        "pf64",
        "sendto",
        "cmd",
        "computername",
        "groupname",
        "wizardhwnd",
        "language",
        "srcexe",
        "uninstallexe",
        "sysuserinfoname",
        "sysuserinfoorg",
        "userinfoname",
        "userinfoorg",
        "userinfoserial",
        "username",
        "log",
    ]
)
PARAMETERIZED_CONSTANTS = frozenset({"cm", "code", "drive", "ini", "param", "reg"})
CONSTANT_RE = re.compile(r"(?<!\{)\{([^{}\r\n]+)\}")
SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*$")
EXPAND_CONSTANT_RE = re.compile(r"\bExpandConstant\s*\(\s*'((?:''|[^'])*)'\s*\)", re.IGNORECASE)
ENVIRONMENT_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*(?:\|.*)?\Z", re.DOTALL)
PREPROCESSOR_RE = re.compile(r"#[A-Za-z_][A-Za-z0-9_]*(?:\s+.*)?\Z", re.DOTALL)


@dataclass(frozen=True)
class ConstantUse:
    line: int
    value: str
    context: str


def _strip_code_comments(line: str, in_brace_comment: bool) -> tuple[str, bool]:
    """Remove Pascal comments while preserving quoted strings."""
    result: list[str] = []
    index = 0
    in_string = False
    while index < len(line):
        char = line[index]
        if in_brace_comment:
            if char == "}":
                in_brace_comment = False
            index += 1
            continue
        if in_string:
            result.append(char)
            if char == "'":
                if index + 1 < len(line) and line[index + 1] == "'":
                    result.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            result.append(char)
        elif char == "{" and not (index + 1 < len(line) and line[index + 1] == "$"):
            in_brace_comment = True
        elif char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        else:
            result.append(char)
        index += 1
    return "".join(result), in_brace_comment


def find_constant_uses(text: str) -> list[ConstantUse]:
    """Find constants in section entries and ExpandConstant string literals."""
    uses: list[ConstantUse] = []
    section = ""
    in_brace_comment = False
    for line_number, original_line in enumerate(text.splitlines(), 1):
        match = SECTION_RE.match(original_line)
        if match:
            section = match.group(1).casefold()
            in_brace_comment = False
            continue

        # Inline preprocessor expressions are handled before Pascal comment
        # stripping because {$...} is a compiler marker, not a constant.
        for token in CONSTANT_RE.finditer(original_line):
            if token.group(1).startswith("#"):
                uses.append(ConstantUse(line_number, token.group(1), "preprocessor"))

        if section == "code":
            line, in_brace_comment = _strip_code_comments(original_line, in_brace_comment)
            for call in EXPAND_CONSTANT_RE.finditer(line):
                literal = call.group(1).replace("''", "'")
                for token in CONSTANT_RE.finditer(literal):
                    if not token.group(1).startswith("#"):
                        uses.append(ConstantUse(line_number, token.group(1), "ExpandConstant"))
            continue

        if original_line.lstrip().startswith(";"):
            continue
        for token in CONSTANT_RE.finditer(original_line):
            if not token.group(1).startswith("#"):
                uses.append(ConstantUse(line_number, token.group(1), f"[{section}]"))
    return uses


def _is_valid(value: str) -> bool:
    folded = value.casefold()
    if folded in SIMPLE_CONSTANTS or value == "\\":
        return True
    if ENVIRONMENT_RE.fullmatch(value) or PREPROCESSOR_RE.fullmatch(value):
        return True
    prefix, separator, argument = folded.partition(":")
    return bool(separator and argument and prefix in PARAMETERIZED_CONSTANTS)


def validate(path: Path) -> list[ConstantUse]:
    return [
        use
        for use in find_constant_uses(path.read_text(encoding="utf-8"))
        if not _is_valid(use.value)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.paths:
        unknown = validate(path)
        if unknown:
            failed = True
            for use in unknown:
                message = (
                    f'{path}:{use.line}: unknown Inno Setup constant "{{{use.value}}}" '
                    f"({use.context})"
                )
                print(
                    message,
                    file=sys.stderr,
                )
        else:
            values = sorted(
                {use.value for use in find_constant_uses(path.read_text(encoding="utf-8"))},
                key=str.casefold,
            )
            print(
                f"{path}: validated {len(values)} constants: "
                + ", ".join(f"{{{value}}}" for value in values)
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
