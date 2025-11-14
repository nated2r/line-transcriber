import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("CHANNEL_SECRET 和 CHANNEL_ACCESS_TOKEN 必須設定在環境變數或 .env 中")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY 必須設定在環境變數或 .env 中")

app = FastAPI()
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
openai_client = OpenAI()

INSTAGRAM_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p|tv)/[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)
YOUTUBE_SHORTS_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/shorts/[A-Za-z0-9_\-]+(?:\?[^\s]*)?",
    re.IGNORECASE,
)


def is_instagram_url(text: str) -> bool:
    return bool(INSTAGRAM_URL_PATTERN.search(text))


def is_youtube_shorts_url(text: str) -> bool:
    return bool(YOUTUBE_SHORTS_URL_PATTERN.search(text))


def run_command(command: list[str]) -> None:
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"指令執行失敗：{' '.join(command)}\nstdout:{completed.stdout}\nstderr:{completed.stderr}"
        )


def download_video(url: str) -> Path:
    work_dir = Path(tempfile.mkdtemp(prefix="ig_dl_"))
    output_template = work_dir / "%(id)s.%(ext)s"
    run_command(["yt-dlp", "-o", str(output_template), url])
    files = list(work_dir.glob("*"))
    if not files:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("下載失敗，未找到影片檔案")
    return files[0]


def extract_audio(video_path: Path) -> Path:
    audio_path = video_path.with_suffix(".wav")
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio_path),
        ]
    )
    return audio_path


def transcribe_audio(audio_path: Path) -> str:
    with audio_path.open("rb") as audio_file:
        response = openai_client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file,
            response_format="text",
        )
    return response


def process_instagram_link(url: str) -> str:
    return process_media_link(url)


def process_youtube_shorts_link(url: str) -> str:
    return process_media_link(url)


def process_media_link(url: str) -> str:
    video_path: Path | None = None
    try:
        video_path = download_video(url)
        audio_path = extract_audio(video_path)
        transcript = transcribe_audio(audio_path)
        return transcript
    finally:
        if video_path:
            shutil.rmtree(video_path.parent, ignore_errors=True)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/callback")
async def callback(
    request: Request,
    x_line_signature: str = Header(default=None),
):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    try:
        handler.handle(body_text, x_line_signature)
    except InvalidSignatureError as exc:
        print("簽章驗證失敗，signature:", x_line_signature)
        print("請求內容:", body_text)
        raise HTTPException(status_code=400, detail="Invalid signature (handler)") from exc

    return JSONResponse(content={"status": "ok"})


@handler.add(MessageEvent)
def handle_message(event: MessageEvent):
    print("收到 LINE 事件：", event)
    if getattr(event.delivery_context, "is_redelivery", False):
        print("偵測到重送事件，已處理過，直接忽略。")
        return
    is_active = getattr(event, "mode", "active") == "active"

    if isinstance(event.message, TextMessage):
        incoming_text = event.message.text.strip()
        if is_instagram_url(incoming_text):
            reply_text = handle_supported_link(
                incoming_text,
                process_instagram_link,
                "IG 連結",
            )
            send_reply(event, reply_text, is_active)
            return
        if is_youtube_shorts_url(incoming_text):
            reply_text = handle_supported_link(
                incoming_text,
                process_youtube_shorts_link,
                "YouTube Shorts 連結",
            )
            send_reply(event, reply_text, is_active)
            return

    default_reply = TextSendMessage(text="請傳送 IG 或 YouTube Shorts 連結，我會幫你轉成文字。")
    if is_active:
        line_bot_api.reply_message(event.reply_token, default_reply)
    else:
        user_id = getattr(event.source, "user_id", None)
        if user_id:
            line_bot_api.push_message(user_id, default_reply)
        else:
            print("standby 事件缺少 user_id，無法推播預設訊息。")


def handle_supported_link(
    url: str,
    processor: Callable[[str], str],
    label: str,
) -> str:
    try:
        transcript = processor(url)
        return f"{label}轉錄結果：\n{transcript}"
    except Exception as exc:
        return f"處理 {label} 時發生錯誤：{exc}"


def send_reply(event: MessageEvent, reply_text: str, is_active: bool) -> None:
    message = TextSendMessage(text=reply_text)
    if is_active:
        line_bot_api.reply_message(event.reply_token, message)
    else:
        user_id = getattr(event.source, "user_id", None)
        if user_id:
            line_bot_api.push_message(user_id, message)
        else:
            print("standby 事件缺少 user_id，無法推播回覆。")

