import os
import asyncio
import time
import subprocess
from pyrogram import Client, filters, enums
from pyrogram.types import BotCommand, Message

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("VideoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Queue system
queue = asyncio.Queue()
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

async def set_commands():
    await bot.set_bot_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "How to use"),
        BotCommand("encode", "Encode video (e.g., /encode 720p)"),
        BotCommand("merge", "Merge videos"),
        BotCommand("screenshot", "Generate a screenshot"),
        BotCommand("softsub", "Add subtitles")
    ])

@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("👋 Hello! I am a high-speed Video Processor.\nSend me a video up to 2GB to begin.")

@bot.on_message(filters.command("help"))
async def help_cmd(client, message):
    help_text = (
        "**Available Commands:**\n"
        "/encode [res] - Encode to 480p, 720p, 1080p, etc.\n"
        "/merge - Reply to multiple videos to merge\n"
        "/screenshot - Generate a frame from video\n"
        "/softsub - Add .srt to video\n\n"
        "**Note:** Files over 2GB are rejected due to Telegram limits."
    )
    await message.reply_text(help_text)

async def process_video(task):
    message, task_type, params = task
    
    # 2GB Check
    file_size = message.video.file_size if message.video else message.document.file_size
    if file_size > MAX_FILE_SIZE:
        await message.reply_text("❌ Error: File size exceeds 2GB limit.")
        return

    status = await message.reply_text("📥 Downloading...")
    path = await bot.download_media(message)
    output = f"processed_{path}"

    try:
        await status.edit_text(f"⚙️ Processing: {task_type}...")
        
        # FFmpeg Logic based on task
        if task_type == "encode":
            # Koyeb Free Tier Tip: Use 'ultrafast' preset to avoid CPU timeouts/crashes
            cmd = f'ffmpeg -i "{path}" -vf scale={params} -c:v libx264 -preset ultrafast -crf 28 -c:a copy "{output}"'
        elif task_type == "screenshot":
            output = "ss.jpg"
            cmd = f'ffmpeg -i "{path}" -ss 00:00:05 -vframes 1 "{output}"'
        
        # Execute FFmpeg
        process = await asyncio.create_subprocess_shell(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        await process.communicate()

        await status.edit_text("📤 Uploading...")
        await bot.send_document(message.chat.id, output)
        await status.delete()

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(path): os.remove(path)
        if os.path.exists(output): os.remove(output)

# Queue Worker
async def worker():
    while True:
        task = await queue.get()
        await process_video(task)
        queue.task_done()

@bot.on_message(filters.video | filters.document)
async def handle_video(client, message):
    # Basic filter to ensure it's a video
    if message.document and not message.document.mime_type.startswith("video/"):
        return
    
    await queue.put((message, "encode", "1280:-1")) # Default to 720p
    await message.reply_text("✅ Added to queue. Processing will start shortly.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(worker())
    bot.run()
