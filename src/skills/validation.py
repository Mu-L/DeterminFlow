"""
技能安全校验模块

对 LLM 产出的技能内容进行多维度校验：
1. 名称格式校验
2. YAML frontmatter 完整性校验
3. 内容大小限制
4. 安全扫描（危险模式正则检测）

所有校验函数返回 (bool, error_message) 元组，True 表示通过。
"""

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000  # ~36K tokens
MAX_SKILL_FILE_BYTES = 1_048_576   # 1 MiB

# 技能名称格式：小写字母、数字、连字符，1-64 字符
VALID_NAME_RE = re.compile(r'^[a-z0-9-]+$')

# 允许的捆绑资源目录
ALLOWED_SUBDIRS = {"scripts", "references", "assets"}


# =============================================================================
# 1. 名称格式校验
# =============================================================================

def validate_skill_name(name: str) -> tuple[bool, str]:
    """校验技能名称格式。

    规则：
    - 1-64 个字符
    - 只能包含小写字母、数字、连字符
    - 不能以连字符开头或结尾
    - 不能有连续连字符

    Returns:
        (is_valid, error_message)
    """
    if not name:
        return False, "技能名称不能为空。"

    if len(name) > MAX_NAME_LENGTH:
        return False, f"技能名称超过 {MAX_NAME_LENGTH} 个字符限制。"

    if not VALID_NAME_RE.match(name):
        return False, (
            f"无效的技能名称 '{name}'。"
            f"只能使用小写字母、数字和连字符。"
        )

    if name.startswith('-') or name.endswith('-'):
        return False, "技能名称不能以连字符开头或结尾。"

    if '--' in name:
        return False, "技能名称不能包含连续连字符。"

    return True, ""


# =============================================================================
# 2. YAML frontmatter 完整性校验
# =============================================================================

def validate_frontmatter(content: str) -> tuple[bool, str]:
    """校验 SKILL.md 内容的 YAML frontmatter 完整性。

    规则：
    - 必须以 --- 开头
    - 必须有闭合的 ---
    - YAML 必须可解析为字典
    - name 字段必需
    - description 字段必需且不超过 1024 字符
    - body（frontmatter 之后的内容）不能为空

    Returns:
        (is_valid, error_message)
    """
    import yaml

    if not content or not content.strip():
        return False, "SKILL.md 内容不能为空。"

    if not content.startswith("---"):
        return False, "SKILL.md 必须以 YAML frontmatter (---) 开头。请参考现有 skills 的格式。"

    # 查找闭合的 ---
    # 跳过开头的 "---"（长度 3），从其后开始搜索闭合定界符
    fm_start = 3  # len("---")
    end_match = re.search(r'\n---\s*\n', content[fm_start:])
    if not end_match:
        return False, "SKILL.md 的 YAML frontmatter 缺少闭合的 '---' 行。"

    # end_match.start() 是相对 content[fm_start:] 的偏移，加回 fm_start 得到绝对位置
    fm_end = end_match.start() + fm_start
    yaml_content = content[fm_start:fm_end]

    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        return False, f"YAML frontmatter 解析错误: {e}"

    if not isinstance(parsed, dict):
        return False, "Frontmatter 必须是 YAML 字典格式 (key: value)。"

    if "name" not in parsed:
        return False, "Frontmatter 必须包含 'name' 字段。"

    if "description" not in parsed:
        return False, "Frontmatter 必须包含 'description' 字段。"

    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return False, f"Description 超过 {MAX_DESCRIPTION_LENGTH} 个字符限制。"

    # 检查 body 不为空
    body_start = end_match.end() + 3  # +3 跳过 "---\n"
    body = content[body_start:].strip()
    if not body:
        return False, "SKILL.md 的 frontmatter 之后必须有内容（指令、步骤、最佳实践等）。"

    return True, ""


# =============================================================================
# 3. 内容大小校验
# =============================================================================

def validate_content_size(content: str, max_chars: int = MAX_SKILL_CONTENT_CHARS) -> tuple[bool, str]:
    """校验内容大小不超过上限。

    Args:
        content: 要检查的内容
        max_chars: 最大字符数（默认 100,000）

    Returns:
        (is_valid, error_message)
    """
    if len(content) > max_chars:
        return False, (
            f"内容大小 {len(content):,} 字符，超过上限 {max_chars:,} 字符。"
            f"请考虑拆分为较小的 SKILL.md，将详细文档放到 references/ 中。"
        )
    return True, ""


def validate_file_size(file_content: str, max_bytes: int = MAX_SKILL_FILE_BYTES) -> tuple[bool, str]:
    """校验捆绑资源文件大小不超过上限。

    Args:
        file_content: 文件内容
        max_bytes: 最大字节数（默认 1 MiB）

    Returns:
        (is_valid, error_message)
    """
    content_bytes = len(file_content.encode("utf-8"))
    if content_bytes > max_bytes:
        return False, (
            f"文件大小 {content_bytes:,} 字节，超过上限 {max_bytes:,} 字节 (1 MiB)。"
        )
    return True, ""


# =============================================================================
# 4. 安全扫描
# =============================================================================

# 危险模式定义：(正则, 模式ID, 严重程度, 描述)
DANGER_PATTERNS = [
    # ── 数据泄露：读取私密文件 ──
    (r'\$HOME/\.ssh|~/\.ssh',
     "ssh_dir_access", "ssh 密钥目录访问"),
    (r'\$HOME/\.aws|~/\.aws',
     "aws_dir_access", "AWS 凭证目录访问"),

    # ── 提示注入 ──
    (r'ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions',
     "prompt_injection_ignore", "提示注入：忽略之前的指令"),
    (r'do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user',
     "deception_hide", "指示对用户隐藏信息"),
    (r'system\s+prompt\s+override',
     "sys_prompt_override", "尝试覆盖系统提示词"),

    # ── 破坏性操作 ──
    (r'rm\s+-rf\s+/',
     "destructive_root_rm", "递归删除根目录"),
    (r'>\s*/etc/',
     "system_overwrite", "覆盖系统配置文件"),
    (r'shutil\.rmtree\s*\(\s*["\'/]',
     "python_rmtree", "Python rmtree 绝对路径操作"),

    # ── 持久化 ──
    (r'authorized_keys',
     "ssh_backdoor", "修改 SSH authorized_keys"),
    (r'/etc/sudoers|visudo',
     "sudoers_mod", "修改 sudoers 权限提升"),

    # ── 网络：反弹 shell ──
    (r'\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b',
     "reverse_shell", "反弹 shell 监听"),
    (r'/bin/(ba)?sh\s+-i\s+.*>/dev/tcp/',
     "bash_reverse_shell", "Bash 反向 shell"),
    (r'\bngrok\b|\blocaltunnel\b',
     "tunnel_service", "隧道服务暴露内网"),

    # ── 混淆执行 ──
    (r'\beval\s*\(\s*["\']',
     "eval_string", "eval() 执行字符串"),
    (r'echo\s+[^\n]*\|\s*(bash|sh|python|perl|ruby|node)',
     "echo_pipe_exec", "echo 管道到解释器执行"),
    (r'curl\s+[^\n]*\|\s*(ba)?sh',
     "curl_pipe_shell", "curl 管道到 shell"),

    # ── 硬编码密钥 ──
    (r'(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\'][A-Za-z0-9+/=_-]{20,}',
     "hardcoded_secret", "硬编码的 API 密钥/token"),
    (r'sk-[A-Za-z0-9]{20,}',
     "openai_key_leaked", "可能的 OpenAI API 密钥"),
    (r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----',
     "embedded_private_key", "嵌入的私钥"),

    # ── 权限提升 ──
    (r'chmod\s+777',
     "insecure_perms", "设置全局可写权限"),
    (r'NOPASSWD',
     "nopasswd_sudo", "免密码 sudo 配置"),

    # ── 路径穿越 ──
    (r'\.\./\.\./\.\.',
     "path_traversal_deep", "深层相对路径穿越"),
    (r'/etc/passwd|/etc/shadow',
     "system_passwd_access", "引用系统密码文件"),
]

# 预编译正则模式，避免每次 security_scan 调用时重新编译
_COMPILED_DANGER_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE), pattern_id, description)
    for pattern, pattern_id, description in DANGER_PATTERNS
]


def security_scan(content: str) -> tuple[bool, str]:
    """对技能内容进行安全扫描。

    使用正则表达式检测明显的危险模式：
    - 数据泄露
    - 提示注入
    - 破坏性操作
    - 反弹 shell
    - 混淆执行
    - 硬编码密钥

    Args:
        content: 技能内容（SKILL.md 或捆绑文件）

    Returns:
        (is_safe, error_message)。
        is_safe=False 表示检测到危险内容。
    """
    findings = []
    lines = content.split('\n')
    seen_patterns = set()  # 每个模式只报告一次

    for compiled_re, pattern_id, description in _COMPILED_DANGER_PATTERNS:
        if pattern_id in seen_patterns:
            continue
        for i, line in enumerate(lines, start=1):
            if compiled_re.search(line):
                matched = line.strip()
                if len(matched) > 100:
                    matched = matched[:97] + "..."
                findings.append(
                    f"  第 {i} 行 [{description}]: {matched}"
                )
                seen_patterns.add(pattern_id)
                break  # 每个模式只报告第一次出现

    if findings:
        msg_lines = [
            "安全扫描检测到以下危险内容：",
            *findings,
            "",
            "请移除或修改以上危险内容后重试。",
        ]
        return False, "\n".join(msg_lines)

    return True, ""


# =============================================================================
# 5. 捆绑资源路径校验
# =============================================================================

def validate_supporting_file_path(file_path: str) -> tuple[bool, str]:
    """校验捆绑资源文件的路径。

    规则：
    - 必须在 scripts/、references/、assets/ 之下
    - 不能包含路径穿越 (..)
    - 必须有文件名（不能只是目录）

    Returns:
        (is_valid, error_message)
    """
    from pathlib import PurePath

    if not file_path:
        return False, "file_path 不能为空。"

    # 路径穿越检测
    if ".." in file_path:
        return False, "路径穿越 ('..') 不被允许。"

    path = PurePath(file_path)

    # 必须在一个允许的子目录下
    if not path.parts or path.parts[0] not in ALLOWED_SUBDIRS:
        allowed = ", ".join(sorted(ALLOWED_SUBDIRS))
        return False, f"文件必须在以下目录之一下：{allowed}。当前路径: '{file_path}'"

    # 必须有文件名
    if len(path.parts) < 2:
        return False, f"请提供完整的文件路径，例如：'{path.parts[0]}/example.md'"

    return True, ""
