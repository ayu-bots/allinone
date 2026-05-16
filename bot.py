import os
import asyncio
import time
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import (
    BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
)

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = os.environ.get("PORT", "8080")

bot = Client("UltraEncoder", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Storage
USER_SETTINGS = {} 
MERGE_QUEUE = {} 
GLOBAL_QUEUE_LIST = [] # To track tasks for /queue cmd
queue = None # Will be initialized in main()

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

# --- Koyeb Health Check ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(PORT)).start()

# --- Helpers ---
def get_user_settings(user_id):
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {
            "prefix": "", "suffix": "", "preset": "ultrafast", 
            "crf": "27", "format": "video"
        }
    return USER_SETTINGS[user_id]

async def get_thumb(path, output):
    cmd = f'ffmpeg -i "{path}" -ss 00:00:05 -vframes 1 "{output}" -y'
    process = await asyncio.create_subprocess_shell(cmd)
    await process.wait()
    return output if os.path.exists(output) else None

# --- Commands ---
@bot.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("🚀 **Advanced Video Bot Online!**\nReply to a video with /encode, /compress, or /screenshot.")

@bot.on_message(filters.command("queue"))
async def show_queue(client, message):
    if not GLOBAL_QUEUE_LIST:
        return await message.reply("The queue is currently empty.")
    text = "**Current Queue:**\n"
    for i, item in enumerate(GLOBAL_QUEUE_LIST):
        text += f"{i+1}. {item['task']} - User: {item['user']}\n"
    await message.reply(text)

@bot.on_message(filters.command("settings"))
async def settings_menu(client, message):
    s = get_user_settings(message.from_user.id)
    text = (f"⚙️ **Settings**\n\nPrefix: `{s['prefix']}`\nSuffix: `{s['suffix']}`\n"
            f"Preset: `{s['preset']}`\nCRF: `{s['crf']}`\nMode: `{s['format']}`")
    buttons = [
        [InlineKeyboardButton("Change Preset", callback_data="set_preset"), InlineKeyboardButton("Change CRF", callback_data="set_crf")],
        [InlineKeyboardButton("Toggle Mode (Doc/Video)", callback_data="toggle_mode")],
        [InlineKeyboardButton("Close", callback_data="close")]
    ]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_message(filters.command(["encode", "compress"]))
async def encode_init(client, message):
    if not message.reply_to_message:
        return await message.reply("❌ Please reply to a video.")
    task = message.command[0]
    keyboard = [
        [InlineKeyboardButton("144p", callback_data=f"enc_144p_{task}"), InlineKeyboardButton("240p", callback_data=f"enc_240p_{task}")],
        [InlineKeyboardButton("360p", callback_data=f"enc_360p_{task}"), InlineKeyboardButton("480p", callback_data=f"enc_480p_{task}")],
        [InlineKeyboardButton("720p", callback_data=f"enc_720p_{task}"), InlineKeyboardButton("1080p", callback_data=f"enc_1080p_{task}")],
        [InlineKeyboardButton("4K", callback_data=f"enc_4k_{task}")]
    ]
    await message.reply(f"Select Quality for {task}:", reply_markup=InlineKeyboardMarkup(keyboard))

@bot.on_message(filters.command("screenshot"))
async def screenshot_init(client, message):
    if not message.reply_to_message:
        return await message.reply("❌ Reply to a video.")
    GLOBAL_QUEUE_LIST.append({"task": "screenshot", "user": message.from_user.id})
    await queue.put((message.from_user.id, message.reply_to_message, "screenshot", "none"))
    await message.reply("✅ Added to Queue: screenshot")

@bot.on_message(filters.command("merge"))
async def merge_init(client, message):
    MERGE_QUEUE[message.from_user.id] = []
    await message.reply("🔄 **Merge Mode On**\nSend videos one by one. I will track the size automatically.")

# --- Merge Logic ---
@bot.on_message((filters.video | filters.document) & filters.private)
async def merge_collector(client, message):
    uid = message.from_user.id
    if uid in MERGE_QUEUE:
        file = message.video or message.document
        if "video" not in file.mime_type: return
        
        # Track existing size
        current_size = sum([os.path.getsize(f) for f in MERGE_QUEUE[uid] if os.path.exists(f)])
        if current_size + file.file_size > MAX_FILE_SIZE:
            return await message.reply("⚠️ Rejecting! Total size would exceed 2GB.")

        status = await message.reply("📥 Downloading part...")
        path = await bot.download_media(message)
        MERGE_QUEUE[uid].append(path)

        buttons = [[InlineKeyboardButton("✅ START MERGE", callback_data=f"start_merge")]]
        await status.edit(f"Added Video {len(MERGE_QUEUE[uid])}. Current total: {round((current_size+file.file_size)/1e6, 2)} MB", reply_markup=InlineKeyboardMarkup(buttons))

# --- FFmpeg Engine ---
async def worker():
    while True:
        uid, msg, action, val = await queue.get()
        s = get_user_settings(uid)
        status = await msg.reply(f"⚙️ Starting {action}...")
        
        file_path = await bot.download_media(msg)
        out_name = f"{s['prefix']}{int(time.time())}{s['suffix']}.mp4"
        out_path = os.path.join(os.getcwd(), out_name)

        try:
            # CPU THROTTLE FIX: -threads 1
            if action == "encode":
                res_map = {"144p":"256:144", "240p":"426:240", "360p":"640:360", "480p":"854:480", "720p":"1280:720", "1080p":"1920:1080", "4k":"3840:2160"}
                scale = res_map.get(val, "1280:720")
                cmd = f'ffmpeg -threads 1 -i "{file_path}" -vf scale={scale} -c:v libx264 -preset {s["preset"]} -crf {s["crf"]} -c:a aac "{out_path}" -y'
            elif action == "screenshot":
                out_path = out_path.replace(".mp4", ".jpg")
                cmd = f'ffmpeg -i "{file_path}" -ss 00:00:05 -vframes 1 "{out_path}" -y'
            elif action == "merge":
                list_file = f"list_{uid}.txt"
                with open(list_file, "w") as f:
                    for p in MERGE_QUEUE[uid]: f.write(f"file '{os.path.abspath(p)}'\n")
                cmd = f'ffmpeg -threads 1 -f concat -safe 0 -i {list_file} -c copy "{out_path}" -y'

            process = await asyncio.create_subprocess_shell(cmd)
            await process.wait()

            # Upload
            thumb = await get_thumb(out_path if action != "screenshot" else file_path, "thumb.jpg")
            if action == "screenshot":
                await bot.send_photo(msg.chat.id, photo=out_path)
            elif s["format"] == "video":
                await bot.send_video(msg.chat.id, video=out_path, thumb=thumb, caption=f"Done: {val}")
            else:
                await bot.send_document(msg.chat.id, document=out_path, thumb=thumb, caption=f"Done: {val}")

        except Exception as e:
            await msg.reply(f"❌ Error: {e}")
        finally:
            # Clean GLOBAL_QUEUE_LIST
            if GLOBAL_QUEUE_LIST: GLOBAL_QUEUE_LIST.pop(0)
            # File cleanup
            for f in [file_path, out_path, "thumb.jpg"]:
                if os.path.exists(f): os.remove(f)
            await status.delete()
            queue.task_done()

# --- Callbacks ---
@bot.on_callback_query()
async def callbacks(client, query: CallbackQuery):
    uid = query.from_user.id
    data = query.data

    if data.startswith("enc_"):
        _, res, action = data.split("_")
        GLOBAL_QUEUE_LIST.append({"task": f"{action} {res}", "user": uid})
        await queue.put((uid, query.message.reply_to_message, action, res))
        await query.message.edit(f"✅ Added {res} to Queue.")
    
    elif data == "start_merge":
        GLOBAL_QUEUE_LIST.append({"task": "merge", "user": uid})
        await queue.put((uid, query.message, "merge", "combined"))
        await query.message.edit("🔄 Merging started...")

# --- Main Entry ---
async def main():
    global queue
    queue = asyncio.Queue() # Initialize inside the running loop
    
    await start_web_server()
    await bot.start()
    
    await bot.set_bot_commands([
        BotCommand("start", "Start Bot"),
        BotCommand("settings", "Settings"),
        BotCommand("merge", "Merge Videos"),
        BotCommand("queue", "Check Queue"),
        BotCommand("screenshot", "Get SS"),
        BotCommand("encode", "Encode (Reply)"),
        BotCommand("compress", "Compress (Reply)")
    ])

    asyncio.create_task(worker())
    print("Bot is fully operational on Koyeb!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
