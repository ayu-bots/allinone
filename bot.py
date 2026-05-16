import os
import asyncio
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import BotCommand

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = os.environ.get("PORT", "8080") # Koyeb provides this

bot = Client("VideoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
queue = asyncio.Queue()
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# --- Koyeb Health Check Server ---
async def handle_health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(PORT))
    await site.start()
    print(f"Health check server started on port {PORT}")

# --- Bot Commands Logic ---
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("🚀 **High-Speed Encoder Bot is Online!**\n\nSend me a video (up to 2GB) to start. I use a queue system to prevent crashes on Koyeb Free Tier.")

@bot.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "**Available Commands:**\n"
        "• `/encode` - 122p to 4k (Reply to video)\n"
        "• `/merge` - Join videos\n"
        "• `/screenshot` - Generate frames\n"
        "• `/softsub` - Add internal subtitles\n\n"
        "**Limit:** 2GB per file."
    )

# --- Queue and Processing ---
async def worker():
    while True:
        message, task_type, resolution = await queue.get()
        try:
            # Check size again before processing
            file_size = (message.video or message.document).file_size
            if file_size > MAX_FILE_SIZE:
                await message.reply_text("❌ File exceeds 2GB. Koyeb/Telegram limit reached.")
                continue

            status = await message.reply_text("📥 Downloading...")
            path = await bot.download_media(message)
            output = f"out_{path}.mp4"

            await status.edit_text(f"⚙️ Encoding to {resolution} (Ultrafast Mode)...")
            
            # Optimized for Koyeb Free Tier (libx264 + ultrafast to save CPU)
            cmd = [
                "ffmpeg", "-i", path,
                "-vf", f"scale={resolution}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                "-c:a", "aac", "-b:a", "128k",
                output, "-y"
            ]
            
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()

            await status.edit_text("📤 Uploading...")
            await bot.send_video(message.chat.id, video=output, caption=f"Done: {resolution}")
            await status.delete()

        except Exception as e:
            await message.reply_text(f"❌ Error: {str(e)}")
        finally:
            if 'path' in locals() and os.path.exists(path): os.remove(path)
            if 'output' in locals() and os.path.exists(output): os.remove(output)
            queue.task_done()

@bot.on_message((filters.video | filters.document) & filters.private)
async def handle_incoming(client, message):
    file = message.video or message.document
    if not file or (message.document and "video" not in message.document.mime_type):
        return

    # THE 2GB CHECK
    if file.file_size > MAX_FILE_SIZE:
        await message.reply_text("⚠️ **File too large!**\nI can only process files up to 2GB.")
        return

    await queue.put((message, "encode", "1280:-1")) # Default 720p
    await message.reply_text("✅ Added to queue. Wait for your turn.")

# --- Main Entry Point ---
async def main():
    # 1. Start Health Check Server (Solves Koyeb TCP issue)
    await start_web_server()
    
    # 2. Sync Commands
    await bot.start()
    await bot.set_bot_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help menu"),
        BotCommand("encode", "Encode video"),
        BotCommand("merge", "Merge videos")
    ])
    
    # 3. Start Queue Worker
    asyncio.create_task(worker())
    
    print("Bot is fully active!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
