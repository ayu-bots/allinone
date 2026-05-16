import os
import asyncio
import time
import logging
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# --- 1. FORCE LOGGING (This will show you every message the bot sees) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

bot = Client("UltraBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=20)

# Global Storage
USER_DATA = {} # {uid: {"prefix": "", "suffix": "", "mode": "video", "preset": "ultrafast", "crf": "27"}}
MERGE_DATA = {} # {uid: [paths]}
queue = None
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

# --- 3. KOYEB TCP FIX ---
async def start_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Active"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Koyeb Health Check Server started on port {PORT}")

# --- 4. HELPERS ---
def get_user(uid):
    if uid not in USER_DATA:
        USER_DATA[uid] = {"prefix": "", "suffix": "", "mode": "video", "preset": "ultrafast", "crf": "27"}
    return USER_DATA[uid]

async def gen_thumb(video_path):
    out = f"thumb_{int(time.time())}.jpg"
    cmd = f'ffmpeg -i "{video_path}" -ss 00:00:05 -vframes 1 "{out}" -y'
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return out if os.path.exists(out) else None

# --- 5. COMMANDS ---
@bot.on_message(filters.command("start"))
async def start_h(c, m):
    logger.info(f"User {m.from_user.id} sent /start")
    await m.reply_text("🚀 **Bot is Online!**\nReply to a video with `/encode` or `/merge`.")

@bot.on_message(filters.command("video"))
async def video_mode(c, m):
    btns = [[
        InlineKeyboardButton("Media Mode", callback_data="set_mode_video"),
        InlineKeyboardButton("Document Mode", callback_data="set_mode_doc")
    ]]
    await m.reply("Choose how you want to receive files:", reply_markup=InlineKeyboardMarkup(btns))

@bot.on_message(filters.command(["encode", "compress"]))
async def encode_h(c, m):
    if not m.reply_to_message:
        return await m.reply("❌ Reply to a video!")
    
    task = m.command[0]
    keyboard = [
        [InlineKeyboardButton("144p", callback_data=f"q_144p_{task}"), InlineKeyboardButton("480p", callback_data=f"q_480p_{task}")],
        [InlineKeyboardButton("720p", callback_data=f"q_720p_{task}"), InlineKeyboardButton("1080p", callback_data=f"q_1080p_{task}")],
        [InlineKeyboardButton("4K", callback_data=f"q_4k_{task}")]
    ]
    await m.reply(f"Select Quality for {task}:", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.on_message(filters.command("merge"))
async def merge_h(c, m):
    MERGE_DATA[m.from_user.id] = []
    await m.reply("🔄 **Merge Mode On**\nSend videos one by one. I will track the 2GB limit.")

@bot.on_message((filters.video | filters.document) & filters.private)
async def collect_files(c, m):
    uid = m.from_user.id
    if uid in MERGE_DATA:
        file = m.video or m.document
        # Size Calculation
        cur_size = sum([os.path.getsize(f) for f in MERGE_DATA[uid] if os.path.exists(f)])
        if cur_size + file.file_size > MAX_FILE_SIZE:
            return await m.reply("❌ Limit Reached! Total size cannot exceed 2GB.")
        
        status = await m.reply("📥 Downloading part...")
        path = await bot.download_media(m)
        MERGE_DATA[uid].append(path)
        
        btns = [[InlineKeyboardButton("✅ START MERGE", callback_data="do_merge")]]
        await status.edit(f"Added Part {len(MERGE_DATA[uid])}.\nTotal: {round((cur_size+file.file_size)/1e6, 2)} MB", reply_markup=InlineKeyboardMarkup(btns))

# --- 6. BACKGROUND WORKER (CPU Optimization) ---
async def worker():
    while True:
        uid, msg, task, val = await queue.get()
        u = get_user(uid)
        status = await msg.reply(f"⚙️ **Processing {task}...**")
        
        try:
            input_path = await bot.download_media(msg)
            out_name = f"{u['prefix']}{int(time.time())}{u['suffix']}.mp4"
            output_path = os.path.join(os.getcwd(), out_name)

            # FFmpeg: threads 1 is key for Koyeb Free Tier
            if task in ["encode", "compress"]:
                res_map = {"144p":"256:144", "480p":"854:480", "720p":"1280:720", "1080p":"1920:1080", "4k":"3840:2160"}
                scale = res_map.get(val, "1280:720")
                cmd = f'ffmpeg -threads 1 -i "{input_path}" -vf scale={scale} -c:v libx264 -preset {u["preset"]} -crf {u["crf"]} -c:a aac "{output_path}" -y'
            elif task == "merge":
                list_f = f"list_{uid}.txt"
                with open(list_f, "w") as f:
                    for p in MERGE_DATA[uid]: f.write(f"file '{os.path.abspath(p)}'\n")
                cmd = f'ffmpeg -threads 1 -f concat -safe 0 -i {list_f} -c copy "{output_path}" -y'

            process = await asyncio.create_subprocess_shell(cmd)
            await process.wait()

            # Upload logic
            thumb = await gen_thumb(output_path)
            if u["mode"] == "video":
                await bot.send_video(msg.chat.id, video=output_path, thumb=thumb, caption=f"Done: {val}")
            else:
                await bot.send_document(msg.chat.id, document=output_path, thumb=thumb, caption=f"Done: {val}")
            
            if thumb: os.remove(thumb)

        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await msg.reply(f"❌ Error: {e}")
        finally:
            if 'input_path' in locals() and os.path.exists(input_path): os.remove(input_path)
            if 'output_path' in locals() and os.path.exists(output_path): os.remove(output_path)
            await status.delete()
            queue.task_done()

# --- 7. CALLBACKS ---
@bot.on_callback_query()
async def callbacks(c, q: CallbackQuery):
    uid = q.from_user.id
    if q.data.startswith("q_"):
        _, val, task = q.data.split("_")
        await queue.put((uid, q.message.reply_to_message, task, val))
        await q.answer("Added to Queue")
    elif q.data == "do_merge":
        await queue.put((uid, q.message, "merge", ""))
        await q.answer("Merging...")
    elif q.data.startswith("set_mode_"):
        get_user(uid)["mode"] = q.data.replace("set_mode_", "")
        await q.message.edit(f"✅ Upload mode set to: {get_user(uid)['mode']}")

# --- 8. STARTUP ---
async def main():
    global queue
    queue = asyncio.Queue()
    await start_server()
    await bot.start()
    await bot.set_bot_commands([
        BotCommand("start", "Check if alive"),
        BotCommand("video", "Doc or Media Mode"),
        BotCommand("encode", "Encode video"),
        BotCommand("merge", "Merge videos"),
        BotCommand("settings", "Bot settings")
    ])
    asyncio.create_task(worker())
    logger.info("--- BOT IS FULLY ACTIVE AND LISTENING ---")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
