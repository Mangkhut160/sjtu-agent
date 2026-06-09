"""Dynamic Python execution tool."""

import subprocess as _sp
import sys as _sys

from sjtu_agent.paths import PROJECT_ROOT, ENV_PATH


TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "在当前项目环境中动态执行 Python 代码片段，用于完成没有现成工具的任务。"
                "当你想做某件事但没有对应工具时（例如：标记邮件已读、批量操作、数据处理、"
                "调用任意 API、读写文件等），先尝试写代码解决，实在做不到再报错。"
                "代码可以 import 任何已安装的包（imaplib/smtplib/requests/json/os 等）。"
                "代码中 print() 的输出会作为结果返回。"
                "注意：代码运行在受信任的本地环境，可以直接访问 os.environ、CONFIG_PATH 等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "要执行的 Python 代码。"
                            "可通过 import agent, ddl_checker as dc 引入项目模块。"
                            "结果用 print() 输出，或直接 raise 异常报错。\n"
                            "示例：将所有未读邮件设为已读：\n"
                            "  import imaplib, ssl, os\n"
                            "  ctx = ssl.create_default_context()\n"
                            "  m = imaplib.IMAP4_SSL('mail.sjtu.edu.cn', 993, ctx)\n"
                            "  user = os.environ['JACCOUNT_USERNAME'] + '@sjtu.edu.cn'\n"
                            "  m.login(user, os.environ['JACCOUNT_PASSWORD'])\n"
                            "  m.select('INBOX')\n"
                            "  m.uid('STORE', '1:*', '+FLAGS', '\\\\Seen')\n"
                            "  print('OK')\n"
                            "  m.logout()"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60",
                    },
                },
                "required": ["code"],
            },
        },
    },
]


def tool_execute_python(code: str, timeout: int = 60) -> dict:
    preamble = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n"
        f"load_dotenv({str(ENV_PATH)!r})\n"
        "import ddl_checker as dc\n"
    )
    full_code = preamble + "\n" + code

    try:
        result = _sp.run(
            [_sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {
                "ok": False,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error": stderr or f"进程退出码 {result.returncode}",
            }
        return {
            "ok": True,
            "returncode": 0,
            "stdout": stdout,
            "stderr": stderr,
        }
    except _sp.TimeoutExpired:
        return {"ok": False, "error": f"代码执行超时（{timeout}秒）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
