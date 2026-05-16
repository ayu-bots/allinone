import os
import asyncio
import time
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = os.environ.get("PORT", "8080")

bot = Client("VideoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
queue = asyncio.Queue()
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# --- Health Check Server (Koyeb Fix) ---
async def handle_health_check(request):
    return web.Response(text="Bot is Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(PORT))
    await site.start()

# --- Helpers ---
def get_resolution(cmd):
    res_map = {
        "144p": "256:144",
        "240p": "426:240",
        "360p": "640:360",
        "480p": "854:480",
        "720p": "1280:720",
        "1080p": "1920:1080",
        "2k": "2560:1440",
        "4k": "3840:2160"
    }
    return res_map.get(cmd, "1280:720")

# --- Commands ---
@bot.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "✨ **High Speed Encoder Bot**\n\n"
        "Send me any video (up to 2GB).\n"
        "Use /encode [res] (e.g., `/encode 720p`) by replying to a video."
    )

@bot.on_message(filters.command("help"))
async def help_cmd(client, message):
    text = (
        "**Available Commands:**\n"
        "• Reply to video with `/encode 480p` (144p to 4k available)\n"
        "• Reply to video with `/screenshot` to get a frame\n"
        "• Reply to video with `/softsub` to add internal subs\n"
        "• `/merge` - (Coming soon/In-Dev)\n\n"
        "**Note:** Files over 2GB will be automatically rejected."
    )
    await message.reply_text(text)

# --- Processing Engine ---
async def worker():
    while True:
        message, task_type, value = await queue.get()
        reply = await message.reply_text("📥 Downloading to Koyeb...")
        
        # Correct Path Handling
        original_path = await bot.download_media(message)
        file_dir = os.path.dirname(original_path)
        file_name = os.path.basename(original_path)
        output_path = os.path.join(file_dir, f"proc_{int(time.time())}_{file_name}.mp4")

        try:
            if task_type == "encode":
                res = get_resolution(value)
                await reply.edit_text(f"⚙️ Encoding to {value}...")
                cmd = [
                    "ffmpeg", "-i", original_path,
                    "-vf", f"scale={res}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "128k",
                    output_path, "-y"
                ]
            
            elif task_type == "screenshot":
                output_path = output_path.replace(".mp4", ".jpg")
                await reply.edit_text("📸 Generating Screenshot...")
                cmd = ["ffmpeg", "-i", original_path, "-ss", "00:00:05", "-vframes", "1", output_path, "-y"]

            elif task_type == "softsub":
                await reply.edit_text("🎬 Adding Softsubs...")
                # Note: This assumes the sub is internal or you have a specific flow. 
                # Basic re-encode for safety:
                cmd = ["ffmpeg", "-i", original_path, "-c", "copy", "-c:s", "mov_text", output_path, "-y"]

            # Execute FFmpeg
            process = await asyncio.create_subprocess_exec(*cmd)
            await process.wait()

            if not os.path.exists(output_path):
                await reply.edit_text("❌ FFmpeg failed to create the file.")
                continue

            await reply.edit_text("📤 Uploading...")
            if task_type == "screenshot":
                await bot.send_photo(message.chat.id, photo=output_path)
            else:
                await bot.send_video(message.chat.id, video=output_path, caption=f"Done: {value}")
            
            await reply.delete()

        except Exception as e:
            await message.reply_text(f"❌ Error: {str(e)}")
        finally:
            if os.path.exists(original_path): os.remove(original_path)
            if os.path.exists(output_path): os.remove(output_path)
            queue.task_done()

# --- Handlers ---
@bot.on_message(filters.command(["encode", "screenshot", "softsub"]))
async def handle_commands(client, message):
    if not message.reply_to_message or not (message.reply_to_message.video or message.reply_to_message.document):
        return await message.reply_text("❌ Please reply to a video file.")

    file = message.reply_to_message.video or message.reply_to_message.document
    
    # 2GB CHECK
    if file.file_size > MAX_FILE_SIZE:
        return await message.reply_text("⚠️ File is too large! Maximum limit is 2GB.")

    cmd_parts = message.text.split()
    task = cmd_parts[0].replace("/", "")
    value = cmd_parts[1] if len(cmd_parts) > 1 else "720p"

    await queue.put((message.reply_to_message, task, value))
    await message.reply_text(f"✅ Added to Queue: {task} ({value if task == 'encode' else ''})")

# --- Startup ---
async def main():
    await start_web_server()
    await bot.start()
    
    # Auto-sync commands
    await bot.set_bot_commands([
        BotCommand("start", "Check if bot is alive"),
        BotCommand("help", "Get usage instructions"),
        BotCommand("encode", "Usage: /encode 720p (reply to video)"),
        BotCommand("screenshot", "Generate SS (reply to video)"),
        BotCommand("softsub", "Add subs (reply to video)")
    ])
    
    asyncio.create_task(worker())
    print("Bot is active and synced!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
