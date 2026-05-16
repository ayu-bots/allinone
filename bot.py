import os
import asyncio
import time
import logging
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- Setup Logging (Fixes "No Logs" Issue) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

bot = Client("UltraBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# State Management
USER_SETTINGS = {} 
MERGE_QUEUE = {} 
queue = None 

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

# --- Koyeb Health Check ---
async def handle_health(request):
    return web.Response(text="Bot is Live")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

# --- Helper Functions ---
def get_s(uid):
    if uid not in USER_SETTINGS:
        USER_SETTINGS[uid] = {"prefix": "", "suffix": "", "preset": "ultrafast", "crf": "27", "format": "video"}
    return USER_SETTINGS[uid]

async def get_thumb(path):
    out = f"thumb_{int(time.time())}.jpg"
    cmd = f'ffmpeg -i "{path}" -ss 00:00:05 -vframes 1 "{out}" -y'
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await process.wait()
    return out if os.path.exists(out) else None

# --- Command Handlers ---
@bot.on_message(filters.command("start"))
async def start_msg(c, m):
    await m.reply_text("👋 **Bot is Online!**\nReply to a video with /encode, /compress or /merge.")

@bot.on_message(filters.command(["encode", "compress"]))
async def encode_cmd(c, m):
    if not m.reply_to_message or not (m.reply_to_message.video or m.reply_to_message.document):
        return await m.reply("❌ Reply to a video file!")
    
    task = m.command[0]
    keyboard = [
        [InlineKeyboardButton("144p", callback_data=f"q_144p_{task}"), InlineKeyboardButton("240p", callback_data=f"q_240p_{task}")],
        [InlineKeyboardButton("360p", callback_data=f"q_360p_{task}"), InlineKeyboardButton("480p", callback_data=f"q_480p_{task}")],
        [InlineKeyboardButton("720p", callback_data=f"q_720p_{task}"), InlineKeyboardButton("1080p", callback_data=f"q_1080p_{task}")],
        [InlineKeyboardButton("4K", callback_data=f"q_4k_{task}")]
    ]
    await m.reply(f"Select Quality for {task}:", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.on_message(filters.command("merge"))
async def merge_start(c, m):
    MERGE_QUEUE[m.from_user.id] = []
    await m.reply("🔄 **Merge Mode Activated**\nSend videos one by one. I'll alert you if size hits 2GB.")

@bot.on_message((filters.video | filters.document) & filters.private)
async def merge_collector(c, m):
    uid = m.from_user.id
    if uid in MERGE_QUEUE:
        file = m.video or m.document
        current_total = sum([os.path.getsize(f) for f in MERGE_QUEUE[uid] if os.path.exists(f)])
        
        if current_total + file.file_size > MAX_FILE_SIZE:
            return await m.reply("❌ **Limit reached!** Adding this file would exceed 2GB.")

        status = await m.reply("📥 Downloading part...")
        path = await bot.download_media(m)
        MERGE_QUEUE[uid].append(path)
        
        btns = [[InlineKeyboardButton("✅ START MERGE", callback_data="do_merge")]]
        await status.edit(f"✅ Added part {len(MERGE_QUEUE[uid])}.\nTotal: {round((current_total+file.file_size)/1e6, 2)}MB", reply_markup=InlineKeyboardMarkup(btns))

# --- Background Worker ---
async def worker():
    while True:
        uid, msg, task, val = await queue.get()
        s = get_s(uid)
        status = await msg.reply(f"⚙️ **Processing {task}...**\n(CPU limited to 1 thread for Koyeb stability)")
        
        file_path = await bot.download_media(msg)
        out_name = f"{s['prefix']}{task}_{int(time.time())}{s['suffix']}.mp4"
        out_path = os.path.join(os.getcwd(), out_name)

        try:
            # CPU THROTTLING: -threads 1 keeps Koyeb from crashing
            if task in ["encode", "compress"]:
                res_map = {"144p":"256:144", "240p":"426:240", "360p":"640:360", "480p":"854:480", "720p":"1280:720", "1080p":"1920:1080", "4k":"3840:2160"}
                scale = res_map.get(val, "1280:720")
                cmd = f'ffmpeg -threads 1 -i "{file_path}" -vf scale={scale} -c:v libx264 -preset {s["preset"]} -crf {s["crf"]} -c:a aac "{out_path}" -y'
            elif task == "merge":
                list_txt = f"list_{uid}.txt"
                with open(list_txt, "w") as f:
                    for p in MERGE_QUEUE[uid]: f.write(f"file '{os.path.abspath(p)}'\n")
                cmd = f'ffmpeg -threads 1 -f concat -safe 0 -i {list_txt} -c copy "{out_path}" -y'

            process = await asyncio.create_subprocess_shell(cmd)
            await process.wait()

            # 2GB Split Check
            if os.path.exists(out_path) and os.path.getsize(out_path) > MAX_FILE_SIZE:
                await status.edit("📏 File is > 2GB. Splitting into parts...")
                # (Simplified split command)
                cmd_split = f'ffmpeg -i "{out_path}" -c copy -map 0 -segment_time 00:50:00 -f segment "part_%03d_{out_name}"'
                await (await asyncio.create_subprocess_shell(cmd_split)).wait()
                # Upload logic for parts would go here
            else:
                thumb = await get_thumb(out_path)
                await bot.send_video(msg.chat.id, video=out_path, thumb=thumb, caption=f"Done: {task} {val}")
                if thumb: os.remove(thumb)

        except Exception as e:
            logger.error(f"Error: {e}")
            await msg.reply(f"❌ Error: {e}")
        finally:
            for f in [file_path, out_path]:
                if os.path.exists(f): os.remove(f)
            await status.delete()
            queue.task_done()

# --- Callback Logic ---
@bot.on_callback_query()
async def cb_handler(c, q: CallbackQuery):
    uid = q.from_user.id
    if q.data.startswith("q_"):
        _, val, task = q.data.split("_")
        await queue.put((uid, q.message.reply_to_message, task, val))
        await q.message.edit(f"✅ Added {task} ({val}) to Queue.")
    elif q.data == "do_merge":
        await queue.put((uid, q.message, "merge", ""))
        await q.message.edit("🔄 Merging started...")

# --- Main Boot ---
async def main():
    global queue
    queue = asyncio.Queue()
    
    logger.info("Initializing bot...")
    await start_web_server()
    await bot.start()
    
    # Auto-sync commands
    await bot.set_bot_commands([
        BotCommand("start", "Check alive"),
        BotCommand("encode", "Encode video"),
        BotCommand("compress", "Compress video"),
        BotCommand("merge", "Merge videos")
    ])
    
    asyncio.create_task(worker())
    logger.info("Bot is fully active and listening!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
