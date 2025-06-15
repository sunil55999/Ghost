import asyncio
import logging
import json
import random
import os
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto
from datetime import datetime
import utils

# Load environment variables
load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID'))

# Configuration
API_ID = 23617139  # Replace with your API ID
API_HASH = "5bfc582b080fa09a1a2eaa6ee60fd5d4"  # Replace with your API hash
SESSION_FILE = "userbot_session"
client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

MAPPINGS_FILE = "channel_mappings.json"
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
MAX_QUEUE_SIZE = 100
NOTIFY_CHAT_ID = None
INACTIVITY_THRESHOLD = 172800  # 48 hours in seconds

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("stealthcopierx.log"), logging.StreamHandler()]
)
logger = logging.getLogger("StealthCopierX")

# Data structures
channel_mappings = {}
message_id_mapping = {}  # {user_id: {pair_name: {source_msg_id: dest_msg_id}}}
is_connected = False
pair_stats = {}

# Helper Functions
def save_mappings():
    """Save channel mappings to a JSON file."""
    try:
        with open(MAPPINGS_FILE, "w") as f:
            json.dump(channel_mappings, f)
        logger.info("Channel mappings saved.")
    except Exception as e:
        logger.error(f"Error saving mappings: {e}")

def load_mappings():
    """Load channel mappings from a JSON file."""
    global channel_mappings
    try:
        with open(MAPPINGS_FILE, "r") as f:
            channel_mappings = json.load(f)
        logger.info(f"Loaded {sum(len(v) for v in channel_mappings.values())} mappings.")
        for user_id, pairs in channel_mappings.items():
            if user_id not in pair_stats:
                pair_stats[user_id] = {}
            for pair_name in pairs:
                pair_stats[user_id][pair_name] = {
                    'copied': 0, 'edited': 0, 'last_activity': None
                }
    except FileNotFoundError:
        logger.info("No mappings file found. Starting fresh.")
    except Exception as e:
        logger.error(f"Error loading mappings: {e}")

async def copy_message(source_msg, dest_channel, pair_config, user_id, pair_name):
    """Copy or edit a message from source to destination with retry logic."""
    try:
        cleaned_text = utils.clean_text(source_msg.text or source_msg.message or "", pair_config)
        if not cleaned_text and not isinstance(source_msg.media, MessageMediaPhoto):
            logger.info(f"Skipping empty message from {source_msg.chat_id}")
            return

        reply_to = await get_reply_to(source_msg, dest_channel, user_id, pair_name)
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                if source_msg.id in message_id_mapping[user_id][pair_name]:
                    await client.edit_message(
                        dest_channel,
                        message_id_mapping[user_id][pair_name][source_msg.id],
                        cleaned_text,
                        reply_to=reply_to
                    )
                    pair_stats[user_id][pair_name]['edited'] += 1
                    logger.info(f"Edited message {source_msg.id} in {dest_channel}")
                else:
                    sent_msg = await client.send_message(
                        dest_channel,
                        cleaned_text,
                        reply_to=reply_to,
                        file=source_msg.media if isinstance(source_msg.media, MessageMediaPhoto) and pair_config.get('copy_images', True) else None
                    )
                    if user_id not in message_id_mapping:
                        message_id_mapping[user_id] = {}
                    if pair_name not in message_id_mapping[user_id]:
                        message_id_mapping[user_id][pair_name] = {}
                    message_id_mapping[user_id][pair_name][source_msg.id] = sent_msg.id
                    pair_stats[user_id][pair_name]['copied'] += 1
                    logger.info(f"Copied message {source_msg.id} to {dest_channel}")
                pair_stats[user_id][pair_name]['last_activity'] = datetime.now().timestamp()
                break
            except Exception as e:
                retry_count += 1
                if retry_count == MAX_RETRIES:
                    logger.error(f"Failed to copy/edit message {source_msg.id} after {MAX_RETRIES} retries: {e}")
                    await client.send_message(NOTIFY_CHAT_ID, f"Failed to process message in pair '{pair_name}' after retries: {e}")
                else:
                    await asyncio.sleep(RETRY_DELAY)
    except Exception as e:
        logger.error(f"Error in copy_message: {e}")

async def get_reply_to(source_msg, dest_channel, user_id, pair_name):
    """Get the destination reply-to message ID if it exists."""
    if source_msg.reply_to_msg_id and user_id in message_id_mapping and pair_name in message_id_mapping[user_id]:
        return message_id_mapping[user_id][pair_name].get(source_msg.reply_to_msg_id)
    return None

# Event Handlers
@client.on(events.NewMessage)
async def handle_new_message(event):
    if not is_connected or not channel_mappings:
        return
    source_chat_id = event.chat_id
    if not source_chat_id:
        return
    source_chat_id = str(source_chat_id)
    msg_text = event.text or event.message or ""
    for user_id, pairs in channel_mappings.items():
        for pair_name, pair_config in pairs.items():
            if pair_config.get('paused', False):
                continue
            if str(pair_config['source']) == source_chat_id:
                await copy_message(event, pair_config['destination'], pair_config, user_id, pair_name)

@client.on(events.MessageEdited)
async def handle_edited_message(event):
    await handle_new_message(event)

# Admin Commands
@client.on(events.NewMessage(pattern=r'/setpair (\S+) (-?\d+) (-?\d+)'))
async def set_pair(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, source, dest = event.pattern_match.group(1), event.pattern_match.group(2), event.pattern_match.group(3)
    if user_id not in channel_mappings:
        channel_mappings[user_id] = {}
    channel_mappings[user_id][pair_name] = {
        'source': int(source),
        'destination': int(dest),
        'paused': False,
        'copy_images': True,
        'header_patterns': [],
        'footer_patterns': [],
        'remove_phrases': [],
        'remove_mentions': False
    }
    save_mappings()
    if user_id not in pair_stats:
        pair_stats[user_id] = {}
    pair_stats[user_id][pair_name] = {'copied': 0, 'edited': 0, 'last_activity': None}
    await event.reply(f"Pair '{pair_name}' set: {source} -> {dest}")

@client.on(events.NewMessage(pattern=r'/pauseall'))
async def pause_all(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    if user_id in channel_mappings:
        for pair_config in channel_mappings[user_id].values():
            pair_config['paused'] = True
        save_mappings()
        await event.reply("All pairs paused.")
    else:
        await event.reply("No pairs to pause.")

@client.on(events.NewMessage(pattern=r'/resumeall'))
async def resume_all(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    if user_id in channel_mappings:
        for pair_config in channel_mappings[user_id].values():
            pair_config['paused'] = False
        save_mappings()
        await event.reply("All pairs resumed.")
    else:
        await event.reply("No pairs to resume.")

@client.on(events.NewMessage(pattern=r'/toggleimagecleaning (\S+)'))
async def toggle_image_cleaning(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name = event.pattern_match.group(1)
    if user_id in channel_mappings and pair_name in channel_mappings[user_id]:
        pair_config = channel_mappings[user_id][pair_name]
        pair_config['copy_images'] = not pair_config.get('copy_images', True)
        save_mappings()
        state = "enabled" if pair_config['copy_images'] else "disabled"
        await event.reply(f"Image copying {state} for pair '{pair_name}'.")
    else:
        await event.reply("Pair not found.")

# New Admin Commands for Filter Configuration
@client.on(events.NewMessage(pattern=r'/addheader (\S+) (.+)'))
async def add_header(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, pattern = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'header_patterns' not in pair_config:
        pair_config['header_patterns'] = []
    if pattern not in pair_config['header_patterns']:
        pair_config['header_patterns'].append(pattern)
        save_mappings()
        await event.reply(f"Header pattern '{pattern}' added to '{pair_name}'")
    else:
        await event.reply(f"Header pattern '{pattern}' already exists in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/addfooter (\S+) (.+)'))
async def add_footer(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, pattern = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'footer_patterns' not in pair_config:
        pair_config['footer_patterns'] = []
    if pattern not in pair_config['footer_patterns']:
        pair_config['footer_patterns'].append(pattern)
        save_mappings()
        await event.reply(f"Footer pattern '{pattern}' added to '{pair_name}'")
    else:
        await event.reply(f"Footer pattern '{pattern}' already exists in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/addremoveword (\S+) (.+)'))
async def add_remove_word(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, phrase = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'remove_phrases' not in pair_config:
        pair_config['remove_phrases'] = []
    if phrase not in pair_config['remove_phrases']:
        pair_config['remove_phrases'].append(phrase)
        save_mappings()
        await event.reply(f"Remove phrase '{phrase}' added to '{pair_name}'")
    else:
        await event.reply(f"Remove phrase '{phrase}' already exists in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/removeheader (\S+) (.+)'))
async def remove_header(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, pattern = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'header_patterns' in pair_config and pattern in pair_config['header_patterns']:
        pair_config['header_patterns'].remove(pattern)
        save_mappings()
        await event.reply(f"Header pattern '{pattern}' removed from '{pair_name}'")
    else:
        await event.reply(f"Header pattern '{pattern}' not found in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/removefooter (\S+) (.+)'))
async def remove_footer(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, pattern = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'footer_patterns' in pair_config and pattern in pair_config['footer_patterns']:
        pair_config['footer_patterns'].remove(pattern)
        save_mappings()
        await event.reply(f"Footer pattern '{pattern}' removed from '{pair_name}'")
    else:
        await event.reply(f"Footer pattern '{pattern}' not found in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/removeword (\S+) (.+)'))
async def remove_word(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name, phrase = event.pattern_match.group(1), event.pattern_match.group(2)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    if 'remove_phrases' in pair_config and phrase in pair_config['remove_phrases']:
        pair_config['remove_phrases'].remove(phrase)
        save_mappings()
        await event.reply(f"Remove phrase '{phrase}' removed from '{pair_name}'")
    else:
        await event.reply(f"Remove phrase '{phrase}' not found in '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/enablementionremoval (\S+)'))
async def enable_mention_removal(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name = event.pattern_match.group(1)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    pair_config['remove_mentions'] = True
    save_mappings()
    await event.reply(f"Mention removal enabled for '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/disablementionremoval (\S+)'))
async def disable_mention_removal(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name = event.pattern_match.group(1)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    pair_config['remove_mentions'] = False
    save_mappings()
    await event.reply(f"Mention removal disabled for '{pair_name}'")

@client.on(events.NewMessage(pattern=r'/showfilters (\S+)'))
async def show_filters(event):
    if event.sender_id != OWNER_ID:
        await event.reply("Unauthorized")
        return
    user_id = str(event.sender_id)
    pair_name = event.pattern_match.group(1)
    if user_id not in channel_mappings or pair_name not in channel_mappings[user_id]:
        await event.reply("Pair not found")
        return
    pair_config = channel_mappings[user_id][pair_name]
    header_patterns = pair_config.get('header_patterns', [])
    footer_patterns = pair_config.get('footer_patterns', [])
    remove_phrases = pair_config.get('remove_phrases', [])
    remove_mentions = pair_config.get('remove_mentions', False)
    filters_str = (
        f"Filters for pair '{pair_name}':\n\n"
        f"Header patterns:\n- " + ("\n- ".join(header_patterns) if header_patterns else "None") + "\n\n"
        f"Footer patterns:\n- " + ("\n- ".join(footer_patterns) if footer_patterns else "None") + "\n\n"
        f"Remove phrases:\n- " + ("\n- ".join(remove_phrases) if remove_phrases else "None") + "\n\n"
        f"Mention removal: {'true' if remove_mentions else 'false'}"
    )
    await event.reply(filters_str)

# Health Monitoring
async def check_inactivity():
    """Monitor pair activity and notify on prolonged inactivity."""
    while True:
        if not channel_mappings or not is_connected:
            await asyncio.sleep(3600)
            continue
        current_time = datetime.now().timestamp()
        for user_id, pairs in channel_mappings.items():
            for pair_name, stats in pair_stats[user_id].items():
                last_activity = stats.get('last_activity')
                if last_activity and (current_time - last_activity) > INACTIVITY_THRESHOLD:
                    await client.send_message(
                        NOTIFY_CHAT_ID,
                        f"Pair '{pair_name}' for user {user_id} has been inactive for over 48 hours."
                    )
        await asyncio.sleep(3600)  # Check every hour

async def main():
    """Start the bot and manage tasks."""
    load_mappings()
    global is_connected, NOTIFY_CHAT_ID
    await client.start()
    is_connected = client.is_connected()
    NOTIFY_CHAT_ID = (await client.get_me()).id
    asyncio.create_task(check_inactivity())
    await client.run_until_disconnected()

if __name__ == "__main__":
    client.loop.run_until_complete(main())
