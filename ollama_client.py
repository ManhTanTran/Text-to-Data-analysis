"""
modules/ollama_client.py
-------------------------
Wrapper gọi Ollama local API.
Dùng cho Text-to-Python visualization (Local-Python-Viz).

Theo paper "Evaluating Local Open-Source LLMs for Privacy-Preserving Data
Visualization via Zero-Shot Prompting", hệ thống dùng Llama3 chạy local qua
Ollama để bảo đảm privacy — không gửi schema/data lên cloud.
"""

import json
import requests
from typing import Optional


# ── Config ──────────────────────────────────────────────────────────────────
OLLAMA_ENDPOINT = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = "llama3.2"          # đổi thành "llama3.2-lite" nếu dùng custom
OLLAMA_TIMEOUT = 120               # giây
OLLAMA_MAX_TOKENS = 512
OLLAMA_TEMPERATURE = 0.1
OLLAMA_TOP_P = 0.9


_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})


def is_ollama_alive() -> bool:
    """Check xem Ollama server có đang chạy không."""
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_local_models() -> list:
    """Liệt kê các model Llama đã pull về."""
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _parse_ollama_response(response: requests.Response) -> str:
    """Parse response từ Ollama (có thể là JSON hoặc NDJSON streaming)."""
    text = response.text.strip()
    if not text:
        return ""

    # Thử parse JSON đơn
    try:
        data = response.json()
        return _extract_text(data)
    except ValueError:
        # NDJSON streaming: lấy dòng cuối có content
        lines = [line for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
                t = _extract_text(data)
                if t:
                    return t
            except ValueError:
                continue
        return text


def _extract_text(data) -> str:
    """Trích xuất text từ response data của Ollama (nhiều format)."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        # Ollama generate API: {"response": "..."}
        if "response" in data:
            return str(data["response"])
        # OpenAI-like: {"choices": [{"text": "..."}]}
        if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                return choice.get("text", choice.get("content", ""))
        # Ollama chat: {"message": {"content": "..."}}
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content", "")
    return str(data) if data else ""


def ollama_complete(
    prompt: str,
    model: str = OLLAMA_MODEL,
    timeout: int = OLLAMA_TIMEOUT,
    max_tokens: int = OLLAMA_MAX_TOKENS,
    temperature: float = OLLAMA_TEMPERATURE,
    top_p: float = OLLAMA_TOP_P,
    stream: bool = False,
) -> str:
    """
    Gọi Ollama generate API.

    Args:
        prompt: Prompt text
        model: Tên model (mặc định llama3.2)
        timeout: Timeout giây
        max_tokens, temperature, top_p: Sampling params
        stream: False = trả về full response, True = streaming

    Returns:
        Generated text string

    Raises:
        RuntimeError nếu Ollama server không respond hoặc trả về rỗng
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        },
    }

    try:
        response = _session.post(OLLAMA_ENDPOINT, json=payload, timeout=timeout)
        response.raise_for_status()
        text = _parse_ollama_response(response).strip()
        if not text:
            raise RuntimeError("Empty response from Ollama")
        return text
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "Cannot connect to Ollama at http://127.0.0.1:11434. "
            "Make sure Ollama is running: 'ollama serve'"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama request timed out after {timeout}s")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Ollama HTTP error: {e}")
