"""SJTU Overleaf (latex.sjtu.edu.cn) 客户端 — Git Bridge + 模板管理。

通过 Overleaf Git Bridge 克隆模板项目，本地套用后编译。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sjtu_agent.paths import DATA_DIR

_OVERLEAF_BASE = "https://latex.sjtu.edu.cn"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "sjtu_templates"
_USER_TEMPLATES_DIR = DATA_DIR / "sjtu_templates"

if sys.platform == "win32":
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
    _STARTUP = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW,
                                       wShowWindow=subprocess.SW_HIDE)
else:
    _NO_WINDOW = 0
    _STARTUP = None


def list_local_templates() -> list[dict]:
    """列出本地可用的模板。内置模板 + 用户下载的模板。"""
    templates = []
    for base in (_TEMPLATES_DIR, _USER_TEMPLATES_DIR):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                readme = d / "README.md"
                desc = ""
                if readme.exists():
                    desc = readme.read_text(encoding="utf-8").strip().split("\n")[0]
                templates.append({
                    "name": d.name,
                    "path": str(d),
                    "description": desc or "(无描述)",
                    "source": "builtin" if str(base) == str(_TEMPLATES_DIR) else "user",
                })
    return templates


def _ensure_templates_dir() -> Path:
    _USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return _USER_TEMPLATES_DIR


def clone_template_from_overleaf(project_id: str, template_name: str = "") -> str | None:
    """通过 Git Bridge 克隆 Overleaf 项目到本地模板目录。返回模板路径。"""
    git = shutil.which("git")
    if not git:
        return None

    name = template_name or f"overleaf-{project_id}"
    target = _USER_TEMPLATES_DIR / name
    if target.exists():
        return str(target)

    url = f"{_OVERLEAF_BASE}/git/{project_id}"
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", url, str(target)],
            capture_output=True, text=True, timeout=60,
            check=True,
        )
        return str(target)
    except subprocess.CalledProcessError:
        return None


def _find_xelatex() -> str | None:
    """查找本机 xelatex。"""
    candidates = [shutil.which("xelatex"), shutil.which("xelatex.exe")]
    if os.name == "nt":
        for d in [r"C:\Program Files\MiKTeX\miktex\bin\x64\xelatex.exe",
                  r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64\xelatex.exe"]:
            if Path(d).exists():
                candidates.insert(0, d)
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def compile_latex(tex_file: Path, work_dir: Path | None = None) -> tuple[bool, str]:
    """运行 xelatex 编译 .tex 文件。返回 (success, output)。"""
    xelatex = _find_xelatex()
    if not xelatex:
        return False, "[xelatex] 未找到 xelatex，请安装 MiKTeX"

    cwd = work_dir or tex_file.parent
    try:
        result = subprocess.run(
            [xelatex, "-interaction=nonstopmode", tex_file.name],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        # xelatex 需要跑两次以生成目录和交叉引用
        subprocess.run(
            [xelatex, "-interaction=nonstopmode", tex_file.name],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        # 提取错误行
        errors = [l for l in result.stdout.split("\n") if l.startswith("!")]
        return False, "\n".join(errors[:5]) or result.stdout[-500:]
    except subprocess.TimeoutExpired:
        return False, "[xelatex] 编译超时"
    except Exception as e:
        return False, f"[xelatex] 异常: {e}"


def find_tex_file(target_dir: Path | None = None) -> Path | None:
    """在目标目录中查找主 .tex 文件。优先级：main.tex > 单个 .tex > None。"""
    from sjtu_agent.paths import PAPERS_DIR
    d = Path(target_dir) if target_dir else PAPERS_DIR
    if not d.exists():
        return None
    tex_files = sorted(d.glob("*.tex"))
    if not tex_files:
        return None
    # 优先 main.tex
    for f in tex_files:
        if f.name.lower() == "main.tex":
            return f
    return tex_files[0]


def push_to_overleaf(project_dir: Path) -> tuple[bool, str]:
    """通过 Git Bridge 将本地改动推送回 Overleaf。返回 (success, message)。"""
    git = shutil.which("git")
    if not git:
        return False, "未找到 git，请安装 Git"

    if not project_dir.exists():
        return False, f"目录不存在: {project_dir}"

    git_dir = project_dir / ".git"
    if not git_dir.exists():
        return False, "该目录不是 git 仓库（没有 .git），请先用 /template clone 从 Overleaf 克隆"

    try:
        # git add -A
        subprocess.run(
            [git, "add", "-A"], cwd=str(project_dir),
            capture_output=True, text=True, timeout=30, check=True,
        )
        # git commit (allow empty in case nothing changed)
        result = subprocess.run(
            [git, "commit", "-m", "Update from sjtu-agent"],
            cwd=str(project_dir),
            capture_output=True, text=True, timeout=30,
        )
        # git push
        push = subprocess.run(
            [git, "push"], cwd=str(project_dir),
            capture_output=True, text=True, timeout=60,
        )
        if push.returncode == 0:
            changed = "nothing to commit" not in result.stdout
            return True, "已推送到 Overleaf" if changed else "没有新的改动，无需推送"
        return False, f"推送失败: {push.stderr[:300] or push.stdout[:300]}"
    except subprocess.TimeoutExpired:
        return False, "推送超时"
    except Exception as e:
        return False, f"推送异常: {e}"


def apply_template(template_name: str, target_dir: Path | None = None) -> str:
    """将模板复制到目标目录，返回操作说明文本。默认使用 PAPERS_DIR。"""
    from sjtu_agent.paths import PAPERS_DIR
    target_dir = Path(target_dir) if target_dir else PAPERS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    template_dir = _TEMPLATES_DIR / template_name
    if not template_dir.exists():
        template_dir = _USER_TEMPLATES_DIR / template_name
    if not template_dir.exists():
        return f"模板 '{template_name}' 不存在。用 /template 查看可用模板。"

    # 复制模板文件（排除 .git, LICENSE, README*, TEMPLATE_GUIDE*）
    copied = 0
    for item in template_dir.iterdir():
        name = item.name
        if name.startswith(".") or name in ("LICENSE",):
            continue
        if name.startswith("README") or name.startswith("TEMPLATE_GUIDE"):
            continue
        dest = target_dir / name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
        copied += 1

    return (
        f"📄 模板 '{template_name}' 已复制到 `{target_dir}`（{copied} 个文件/目录）。\n\n"
        f"把你的论文/文档文件放到这个目录，然后说「帮我格式化」即可。\n"
        f"可通过环境变量 `SJTU_PAPERS_DIR` 自定义目录。"
    )
