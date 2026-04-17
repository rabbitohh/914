import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "preview.html"
ENV_FILE = BASE_DIR / ".env"
HOST = os.environ.get("HOST", "0.0.0.0").strip()
PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
SYSTEM_PROMPT = "你是SCP-914 万能转换机。直接给出结果，不要展示思考过程，不要解释。以下内容作为样例。\n附录：5/14：█████博士的测试记录\n输入：1kg钢铁（粗加工档）\n输出：一堆大小不一的钢块，看起来是被激光所切割。\n输入：1kg钢铁（1:1档）\n输出：1kg钢螺钉\n输入：1kg钢铁（精加工档）\n输出：1kg钢地毯钉\n输入：1kg钢铁（超精加工档）\n输出：一些在空气中快速消散的气体；1g未知金属，可抵抗50000度的高温，不会在任何外力下弯曲或破坏，有着近乎完美（1.6x10-75 ρ）的导电性\n输入：一个█████博士的手表（半粗加工档）。\n输出：一个被完全拆散的手表\n输入：一个█████的手机（1:1档）\n输出：一个手机，但款式和品牌不相同\n输入：一把柯尔特蟒蛇型左轮手枪（超精加工档）\n输出：[数据删除]。前述的████████████完全粉碎了其弹道上的一切物质。该物体包含高密度的伽马射线。\n输入：一只白老鼠（1:1档）\n输出：一只棕老鼠\n输入：一只黑猩猩（精加工档）\n输出：[数据删除\n输入：一只黑猩猩（粗加工档）\n输出：严重残缺的尸体，有着被挤压和高热切割的痕迹\n文件#109-B:117：███博士和███████博士的测试记录\n输入：人员D-186，白种男性，42岁，108kg，身高185cm（1:1档）\n输出：拉丁裔男性，42岁，100kg，身高188cm。对象非常困惑和激动。对象攻击安保人员。对象被处决。\n输入：人员D-187，白种男性，28岁，63kg，身高173cm（超精加工档）\n输出：[数据删除]。对象从测试间逃离，杀死了███博士和███████博士以及八个守卫。一级防范禁闭措施启动。对象在持续试图逃离的过程中造成了三个SCP区域的收容失效。特殊反应小组与对象交战，交战结果为对象遭受重创，数个特殊反应小组成员受到部分记忆损伤，管道装置受到腐蚀性损伤。对象于数小时后死亡，尸体分解为蓝色粉末并使附近的研究小组成员失明。\n这些样例仅仅供你参考，请绝对不要照搬。"
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.2


def load_env_file():
    if not ENV_FILE.exists():
        return

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_prompt(user_input, setting):
    return (
        "接下来，你扮演SCP-914 万能转换机，我给你输入和设定，"
        "告诉我输出物(不要有“输出”字样) 。\n"
        "若设定为1:1档，则输出物和输入物在功能上相似，但不完全相同\n"
        f"输入：{user_input}\n"
        f"设定：{setting}"
    )


def describe_network_error(error):
    reason = getattr(error, "reason", error)

    if isinstance(reason, ConnectionResetError) or getattr(reason, "winerror", None) == 10054:
        return "连接被远程主机重置（WinError 10054）"

    if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
        return "连接超时"

    if isinstance(reason, OSError) and getattr(reason, "errno", None) is not None:
        return f"网络错误（errno {reason.errno}）: {reason}"

    return str(reason)


def is_retryable_error(error):
    reason = getattr(error, "reason", error)

    if isinstance(reason, (ConnectionResetError, TimeoutError, socket.timeout)):
        return True

    if isinstance(reason, OSError) and getattr(reason, "winerror", None) == 10054:
        return True

    if isinstance(reason, OSError) and getattr(reason, "errno", None) in {54, 104, 110, 111}:
        return True

    return False


def request_deepseek(user_input, setting):
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    api_url = os.environ.get("DEEPSEEK_API_URL", DEFAULT_DEEPSEEK_API_URL).strip()
    if not api_key:
        raise ValueError("后端未配置 DEEPSEEK_API_KEY，请先填写 .env 文件。")

    payload = {
        "model": DEEPSEEK_MODEL,
        "thinking": {"type": "disabled"},
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": build_prompt(user_input, setting),
            },
        ],
    }

    request_body = json.dumps(payload).encode("utf-8")
    last_error_message = None

    for attempt in range(1, MAX_RETRIES + 1):
        request = Request(
            api_url,
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "scp914-local-server/1.0",
                "Connection": "close",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if not content:
                    raise RuntimeError("DeepSeek 返回成功，但没有文本内容。")
                return content
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(raw)
                message = error_data.get("error", {}).get("message") or raw
            except json.JSONDecodeError:
                message = raw or f"DeepSeek 请求失败（HTTP {exc.code}）"
            raise RuntimeError(message) from exc
        except URLError as exc:
            last_error_message = describe_network_error(exc)
            if attempt < MAX_RETRIES and is_retryable_error(exc):
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise RuntimeError(
                f"无法连接 DeepSeek：{last_error_message}。"
                f"已重试 {attempt} 次。"
                "如果这个错误经常出现，通常是网络波动、代理/防火墙干扰，"
                "或远端临时断开连接。"
            ) from exc
        except (ConnectionResetError, TimeoutError, socket.timeout, OSError) as exc:
            last_error_message = describe_network_error(exc)
            if attempt < MAX_RETRIES and is_retryable_error(exc):
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise RuntimeError(
                f"无法连接 DeepSeek：{last_error_message}。"
                f"已重试 {attempt} 次。"
                "如果这个错误经常出现，通常是网络波动、代理/防火墙干扰，"
                "或远端临时断开连接。"
            ) from exc

    raise RuntimeError(f"无法连接 DeepSeek：{last_error_message or '未知网络错误'}。")


class SCP914Handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        if not HTML_FILE.exists():
            self.send_error(404, "preview.html not found")
            return

        body = HTML_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/preview.html"):
            self._send_html()
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path != "/api/transform":
            self._send_json(404, {"error": "接口不存在。"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体不是合法 JSON。"})
            return

        user_input = str(data.get("input", "")).strip()
        setting = str(data.get("setting", "")).strip()

        if not user_input:
            self._send_json(400, {"error": "请输入待处理对象。"})
            return

        if not setting:
            self._send_json(400, {"error": "请选择设定。"})
            return

        try:
            result = request_deepseek(user_input, setting)
        except ValueError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})
            return
        except Exception:
            self._send_json(500, {"error": "服务内部错误。"})
            return

        self._send_json(200, {"result": result})

    def log_message(self, format, *args):
        return


def main():
    load_env_file()
    server = ThreadingHTTPServer((HOST, PORT), SCP914Handler)
    print(f"SCP-914 server running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
