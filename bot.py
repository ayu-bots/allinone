import os
import asyncio
import time
import subprocess
from aiohttp import web
from pyrogram import Client, filters, enums
from pyrogram.types import (
    BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
)

# --- Config ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = os.environ.get("PORT", "8080")

bot = Client("UltraEncoder", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
queue = asyncio.Queue()

# Memory storage for user states
USER_SETTINGS = {} # {user_id: {"prefix": "", "suffix": "", "preset": "ultrafast", "crf": "27", "format": "video"}}
MERGE_QUEUE = {} # {user_id: [list of file_paths]}

MAX_FILE_SIZE = 2000 * 1024 * 1024 # Approx 2GB

# --- Koyeb Port Fix ---
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(PORT)).start()

# --- Utility Functions ---
def get_user_settings(user_id):
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {
            "prefix": "", "suffix": "", "preset": "ultrafast", 
            "crf": "27", "format": "video", "thumb": True
        }
    return USER_SETTINGS[user_id]

async def get_thumb(path, output):
    # Generates a thumbnail at 5s mark
    cmd = f'ffmpeg -i "{path}" -ss 00:00:05 -vframes 1 "{output}" -y'
    proc = await asyncio.create_subprocess_shell(cmd)
    await proc.wait()
    return output if os.path.exists(output) else None

# --- Command: Settings ---
@bot.on_message(filters.command("settings"))
async def settings_cmd(client, message):
    s = get_user_settings(message.from_user.id)
    text = (f"⚙️ **Settings**\n\nPrefix: `{s['prefix']}`\nSuffix: `{s['suffix']}`\n"
            f"Preset: `{s['preset']}`\nCRF: `{s['crf']}`\nMode: `{s['format']}`")
    
    buttons = [
        [InlineKeyboardButton("Set Preset", callback_data="set_preset"),
         InlineKeyboardButton("Set CRF", callback_data="set_crf")],
        [InlineKeyboardButton("Toggle Mode (Doc/Video)", callback_data="toggle_mode")],
        [InlineKeyboardButton("Close", callback_data="close")]
    ]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# --- Command: Encode & Compress ---
@bot.on_message(filters.command(["encode", "compress"]))
async def encode_init(client, message):
    if not message.reply_to_message:
        return await message.reply("Reply to a video!")
    
    task = message.command[0]
    keyboard = [
        [InlineKeyboardButton("144p", callback_data=f"enc_144p_{task}"), InlineKeyboardButton("240p", callback_data=f"enc_240p_{task}")],
        [InlineKeyboardButton("360p", callback_data=f"enc_360p_{task}"), InlineKeyboardButton("480p", callback_data=f"enc_480p_{task}")],
        [InlineKeyboardButton("720p", callback_data=f"enc_720p_{task}"), InlineKeyboardButton("1080p", callback_data=f"enc_1080p_{task}")],
        [InlineKeyboardButton("4K", callback_data=f"enc_4k_{task}")]
    ]
    await message.reply("Choose Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Command: Merge ---
@bot.on_message(filters.command("merge"))
async def merge_init(client, message):
    user_id = message.from_user.id
    MERGE_QUEUE[user_id] = []
    await message.reply("🔄 **Merge Mode Active**\nSend videos one by one. I will track the size.")

@bot.on_message((filters.video | filters.document) & filters.private)
async def collector(client, message):
    uid = message.from_user.id
    if uid in MERGE_QUEUE:
        file = message.video or message.document
        # Check size
        current_total = sum([os.path.getsize(f) for f in MERGE_QUEUE[uid] if os.path.exists(f)])
        if current_total + file.file_size > MAX_FILE_SIZE:
            return await message.reply("⚠️ Total size will exceed 2GB! File rejected.")
        
        status = await message.reply("📥 Downloading for merge...")
        path = await bot.download_media(message)
        MERGE_QUEUE[uid].append(path)
        
        # Show list buttons
        btns = []
        for i, p in enumerate(MERGE_QUEUE[uid]):
            btns.append([InlineKeyboardButton(f"❌ Remove {i+1}", callback_data=f"rem_{uid}_{i}")])
        btns.append([InlineKeyboardButton("✅ START MERGE", callback_data=f"start_merge_{uid}")])
        
        await status.edit(f"Added Video {len(MERGE_QUEUE[uid])}. Total size: {round((current_total+file.file_size)/1048576, 2)} MB", 
                         reply_markup=InlineKeyboardMarkup(btns))

# --- Processing Engine ---
async def process_task(task_data):
    # task_data format: (user_id, message, type, value)
    uid, msg, action, val = task_data
    s = get_user_settings(uid)
    
    status = await msg.reply("🚀 Processing started...")
    file_path = await bot.download_media(msg)
    
    # 100% CPU Fix: We use -threads 1 to prevent Koyeb from killing us
    # Also added Prefix/Suffix logic
    base_name = f"{s['prefix']}{val}_{msg.from_user.id}{s['suffix']}.mp4"
    out_path = os.path.join(os.getcwd(), base_name)
    
    if action == "encode":
        res_map = {"144p":"256:144", "240p":"426:240", "360p":"640:360", "480p":"854:480", "720p":"1280:720", "1080p":"1920:1080", "4k":"3840:2160"}
        scale = res_map.get(val, "1280:720")
        cmd = f'ffmpeg -threads 1 -i "{file_path}" -vf scale={scale} -c:v libx264 -preset {s["preset"]} -crf {s["crf"]} -c:a aac "{out_path}" -y'
    
    elif action == "merge":
        # Merge uses a concat file
        list_file = f"list_{uid}.txt"
        with open(list_file, "w") as f:
            for p in MERGE_QUEUE[uid]:
                f.write(f"file '{os.path.abspath(p)}'\n")
        cmd = f'ffmpeg -threads 1 -f concat -safe 0 -i {list_file} -c copy "{out_path}" -y'

    proc = await asyncio.create_subprocess_shell(cmd)
    await proc.wait()

    # Split if > 2GB
    if os.path.exists(out_path) and os.path.getsize(out_path) > MAX_FILE_SIZE:
        await status.edit("📏 File > 2GB. Splitting now...")
        # Split logic simplified for brevity: segmenting
        split_cmd = f'ffmpeg -i "{out_path}" -c copy -map 0 -segment_time 00:50:00 -f segment "part_%03d_{base_name}"'
        await (await asyncio.create_subprocess_shell(split_cmd)).wait()
        # Upload all parts...
    else:
        # Standard Upload
        thumb = await get_thumb(out_path, "thumb.jpg")
        if s["format"] == "video":
            await bot.send_video(msg.chat.id, video=out_path, thumb=thumb, caption=f"Done!")
        else:
            await bot.send_document(msg.chat.id, document=out_path, thumb=thumb, caption=f"Done!")

    # Cleanup
    for f in [file_path, out_path, "thumb.jpg"]:
        if os.path.exists(f): os.remove(f)
    await status.delete()

# --- Callback Handler ---
@bot.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    data = query.data
    uid = query.from_user.id

    if data.startswith("enc_"):
        _, res, action = data.split("_")
        await queue.put((uid, query.message.reply_to_message, "encode", res))
        await query.answer("Added to Queue!")
        await query.message.delete()

    elif data.startswith("start_merge"):
        await queue.put((uid, query.message, "merge", "combined"))
        await query.answer("Merging started...")

# --- Main ---
async def main():
    await start_web_server()
    await bot.start()
    await bot.set_bot_commands([
        BotCommand("start", "Start"), BotCommand("settings", "Bot settings"),
        BotCommand("merge", "Combine videos"), BotCommand("encode", "Encode video")
    ])
    
    # Worker
    async def worker():
        while True:
            task = await queue.get()
            await process_task(task)
            queue.task_done()
    
    asyncio.create_task(worker())
    print("Bot is ready for Koyeb/VPS")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
