"""
Telegram Bio & Group Link Guardian Bot
Powered by python-telegram-bot + Async MongoDB (Motor) + Render Health Server.
Features Interactive Button UI, Spoiler Welcome Banner, Control Panel, & Broadcast.
"""

import os
import time
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional

from aiohttp import web
from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from database import db
from filters import bio_has_violation, message_has_violation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [BIOLINK_GUARDIAN] %(message)s",
)
logger = logging.getLogger("biolink_guardian_bot")

# Cache user bios in memory for BIO_CACHE_TTL_SECONDS to avoid rate limits:
# user_id -> (bio_text, has_personal_channel, expiry_timestamp)
BIO_CACHE: Dict[int, Tuple[str, bool, float]] = {}


# ==========================================================
# UI Helpers & Audit Log Dispatcher
# ==========================================================

def get_support_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Updates 📢", url=Config.SUPPORT_CHANNEL_URL),
                InlineKeyboardButton("Support 💬", url=Config.SUPPORT_CHANNEL_URL),
            ]
        ]
    )


def get_welcome_content(
    sender_id: int, sender_name: str, bot_username: str
) -> Tuple[str, InlineKeyboardMarkup]:
    caption = (
        f'👋 Hey, - <a href="tg://user?id={sender_id}">{sender_name}</a>\n\n'
        "<blockquote>• <b>Bio &amp; Link Guardian Bot</b> keeps your group safe &amp; clean!\n\n"
        "🚫 Auto-deletes messages from users with links or @usernames in their bio.\n"
        "🛑 Auto-deletes group messages containing links or @username promotions.\n\n"
        "🌟 Add me to your group with <b>Delete Messages</b> rights!</blockquote>"
    )
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Add me to your group",
                    url=f"https://t.me/{bot_username}?startgroup=true",
                )
            ],
            [InlineKeyboardButton("❓ Help and commands", callback_data="help_menu")],
            [
                InlineKeyboardButton("Updates 📢", url=Config.SUPPORT_CHANNEL_URL),
                InlineKeyboardButton("Support 💬", url=Config.SUPPORT_CHANNEL_URL),
            ],
        ]
    )
    return caption, buttons


def get_help_content() -> Tuple[str, InlineKeyboardMarkup]:
    help_text = (
        "⚙️ <b>Help and commands</b>\n\n"
        "<blockquote>🛡️ <b>Bio Link &amp; @Username Auto-Deletion Shield:</b>\n"
        "• Real-time scan of group members' profile bios and linked channels.\n"
        "• Automatically deletes messages from anyone keeping URLs or @usernames in their bio.\n\n"
        "🚫 <b>Group Link &amp; Mention Guardian:</b>\n"
        "• Instantly deletes links, hidden text URLs, and @username promotions sent in the group chat.\n\n"
        "⚡ <b>Commands:</b>\n"
        "• <code>/biolink on</code> | <code>/biolink off</code> — Toggle Bio Link &amp; @Username Shield\n"
        "• <code>/antilink on</code> | <code>/antilink off</code> — Toggle Group Link Deletion\n"
        "• <code>/antimention on</code> | <code>/antimention off</code> — Toggle Group @Username Deletion\n"
        "• <code>/settings</code> — Interactive group protection control panel\n"
        "• <code>/whitelist</code> — Reply to a user to exempt them from filters\n"
        "• <code>/unwhitelist</code> — Reply to a user to remove exemption\n"
        "• <code>/stats</code> — Real-time scan &amp; deletion statistics\n\n"
        "✅ Just add me to your group &amp; grant <b>Delete Messages</b> rights!</blockquote>"
    )
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("« Back", callback_data="back_start")]]
    )
    return help_text, buttons


async def render_settings_view(chat_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    settings = await db.get_group_config(chat_id)
    bio_on = bool(settings.get("bio_link_filter", True))
    link_on = bool(settings.get("msg_link_filter", True))
    mention_on = bool(settings.get("msg_username_filter", False))
    action = str(settings.get("action", Config.DEFAULT_ACTION)).upper()

    bio_icon = "🟢 ON" if bio_on else "🔴 OFF"
    link_icon = "🟢 ON" if link_on else "🔴 OFF"
    mention_icon = "🟢 ON" if mention_on else "🔴 OFF"

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Bio Shield: {bio_icon}", callback_data="toggle_bio"
                ),
                InlineKeyboardButton(
                    f"Link Shield: {link_icon}", callback_data="toggle_link"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"@Mention Shield: {mention_icon}", callback_data="toggle_mention"
                ),
            ],
            [
                InlineKeyboardButton("Delete", callback_data="act_delete"),
                InlineKeyboardButton("Warn", callback_data="act_warn"),
                InlineKeyboardButton("Mute", callback_data="act_mute"),
                InlineKeyboardButton("Ban", callback_data="act_ban"),
            ],
        ]
    )

    text = (
        "⚙️ <b>Group Protection Control Panel:</b>\n\n"
        f"• <b>Bio Shield (Bio Links/@Usernames):</b> <code>{bio_icon}</code>\n"
        f"• <b>Link Shield (Group Chat URLs):</b> <code>{link_icon}</code>\n"
        f"• <b>@Mention Shield (Chat @Usernames):</b> <code>{mention_icon}</code>\n"
        f"• <b>Penalty Action Mode:</b> <code>{action}</code>\n\n"
        "<i>Tap buttons below to toggle features instantly!</i>"
    )
    return text, buttons


async def send_log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send audit log message to the configured LOG_GROUP_ID."""
    if not Config.LOG_GROUP_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=Config.LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as exc:
        logger.warning(
            "Failed to dispatch message to log group (%s): %s",
            Config.LOG_GROUP_ID,
            exc,
        )


async def check_admin_and_auto_leave(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, chat_title: str
) -> bool:
    """If bot is not an admin in a group, automatically warn, leave, and send log."""
    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat_id, me.id)
        if member.status not in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            logger.warning(
                "Bot is not admin in %s (%s). Auto-leaving...", chat_title, chat_id
            )
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Administrator Rights Missing / Revoked!</b>\n\n"
                        "Since my administrator permissions were removed or not granted, I am leaving the group.\n"
                        "Please re-add me as Administrator with <b>Delete Messages</b> permission to protect your chat!"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass

            try:
                await context.bot.leave_chat(chat_id)
            except TelegramError:
                pass

            await db.remove_group(chat_id)
            await send_log(
                context,
                f"🚪 <b>#AUTO_LEFT_GROUP</b>\n\n"
                f"👥 <b>Group Name:</b> <b>{chat_title}</b>\n"
                f"🆔 <b>Group ID:</b> <code>{chat_id}</code>\n"
                f"⚠️ <b>Reason:</b> Missing Administrator rights, auto-left group.",
            )
            return True
    except TelegramError as exc:
        logger.debug("Error checking bot admin permissions in %s: %s", chat_id, exc)
    return False


async def is_authorized_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None
) -> bool:
    """Check if the sender is the bot owner or a group admin."""
    chat = update.effective_chat
    user = update.effective_user
    uid = user_id or (user.id if user else 0)
    if not uid:
        return False
    if Config.OWNER_ID and uid == Config.OWNER_ID:
        return True
    if chat and chat.type == ChatType.PRIVATE:
        return True
    if not chat:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, uid)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except TelegramError:
        return False


async def is_user_exempt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    if await is_authorized_admin(update, context, user_id):
        return True
    chat = update.effective_chat
    if not chat:
        return False
    whitelisted = await db.get_whitelisted_users(chat.id)
    return user_id in whitelisted


async def fetch_user_bio(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> Tuple[str, bool]:
    now = time.time()
    cached = BIO_CACHE.get(user_id)
    if cached and cached[2] > now:
        await db.increment_stat("cache_hits")
        return cached[0], cached[1]

    try:
        user_chat = await context.bot.get_chat(user_id)
        bio_text = (user_chat.bio or "").strip()
        has_personal_channel = bool(getattr(user_chat, "personal_chat", None))
        BIO_CACHE[user_id] = (
            bio_text,
            has_personal_channel,
            now + Config.BIO_CACHE_TTL_SECONDS,
        )
        return bio_text, has_personal_channel
    except TelegramError as exc:
        logger.debug("Failed to fetch bio for user %s: %s", user_id, exc)
        return "", False


async def auto_delete_msg(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    delay_sec: int = 15,
) -> None:
    """Asynchronously delete warning message after delay."""
    await asyncio.sleep(delay_sec)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


# ==========================================================
# Core Group Message & Bio Moderation
# ==========================================================

async def enforce_moderation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    violation_reason: str,
    is_bio_violation: bool,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return

    try:
        await message.delete()
    except TelegramError as exc:
        logger.warning("Failed to delete message in chat %s: %s", chat.id, exc)
        await check_admin_and_auto_leave(context, chat.id, chat.title or "Group")
        return

    await db.increment_stat("bio_deleted" if is_bio_violation else "msg_deleted")

    group_cfg = await db.get_group_config(chat.id)
    action = group_cfg.get("action", Config.DEFAULT_ACTION)
    max_warnings = int(group_cfg.get("max_warnings", Config.MAX_WARNINGS))
    mute_minutes = int(group_cfg.get("mute_minutes", Config.MUTE_DURATION_MINUTES))

    mention_html = user.mention_html()
    fix_hint = (
        "Remove any links or @usernames from your Telegram bio to chat in this group."
        if is_bio_violation
        else "Sending links or @username promotions is not allowed in this group."
    )

    if action == "delete":
        notice_text = (
            f"🚫 {mention_html}, your message was auto-deleted!\n"
            f"<blockquote>• <b>Reason:</b> <code>{violation_reason}</code>\n"
            f"• <b>Note:</b> {fix_hint}</blockquote>"
        )
    elif action == "warn":
        warn_count = await db.increment_warning(chat.id, user.id, violation_reason)
        if warn_count >= max_warnings:
            await db.reset_warnings(chat.id, user.id)
            until_dt = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes)
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_dt,
                )
                notice_text = (
                    f"🔇 {mention_html} reached <b>{warn_count}/{max_warnings}</b> warnings "
                    f"and was muted for <b>{mute_minutes} minutes</b>!\n"
                    f"<blockquote>• <b>Reason:</b> <code>{violation_reason}</code></blockquote>"
                )
            except TelegramError:
                notice_text = (
                    f"⚠️ {mention_html} reached <b>{warn_count}/{max_warnings}</b> warnings "
                    f"(<code>{violation_reason}</code>)."
                )
        else:
            notice_text = (
                f"⚠️ {mention_html}, warning <b>{warn_count}/{max_warnings}</b>!\n"
                f"<blockquote>• <b>Reason:</b> <code>{violation_reason}</code>\n"
                f"• <b>Note:</b> {fix_hint}</blockquote>"
            )
    elif action == "mute":
        until_dt = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_dt,
            )
            notice_text = (
                f"🔇 {mention_html} was muted for <b>{mute_minutes} minutes</b>!\n"
                f"<blockquote>• <b>Reason:</b> <code>{violation_reason}</code>\n"
                f"• <b>Note:</b> {fix_hint}</blockquote>"
            )
        except TelegramError:
            notice_text = (
                f"🚫 {mention_html}, your message was removed (<code>{violation_reason}</code>)."
            )
    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            notice_text = (
                f"🔨 {mention_html} was banned from the group!\n"
                f"<blockquote>• <b>Reason:</b> <code>{violation_reason}</code></blockquote>"
            )
        except TelegramError:
            notice_text = (
                f"🚫 {mention_html}, your message was removed (<code>{violation_reason}</code>)."
            )
    else:
        notice_text = (
            f"🚫 {mention_html}, your message was removed (<code>{violation_reason}</code>)."
        )

    try:
        sent = await context.bot.send_message(
            chat_id=chat.id,
            text=notice_text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_support_buttons(),
        )
        if Config.WARN_DELETE_SECONDS > 0:
            asyncio.create_task(
                auto_delete_msg(
                    context, chat.id, sent.message_id, Config.WARN_DELETE_SECONDS
                )
            )
    except TelegramError as exc:
        logger.debug("Could not send warning message in %s: %s", chat.id, exc)


async def handle_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user.is_bot:
        return

    await db.increment_stat("total_scans")

    # Skip Group Admins, Bot Owner, or Whitelisted Members
    if await is_user_exempt(update, context, user.id):
        return

    group_cfg = await db.get_group_config(chat.id)

    # 1. Check message content for links or @username promotions
    msg_reason: Optional[str] = message_has_violation(
        message,
        check_links=bool(group_cfg.get("msg_link_filter", True)),
        check_usernames=bool(group_cfg.get("msg_username_filter", False)),
    )
    if msg_reason:
        await enforce_moderation(update, context, msg_reason, is_bio_violation=False)
        return

    # 2. Check user's profile bio for links or @username links
    check_bio_links = bool(group_cfg.get("bio_link_filter", True))
    check_bio_usernames = bool(group_cfg.get("bio_username_filter", True))

    if check_bio_links or check_bio_usernames:
        bio_text, has_personal_channel = await fetch_user_bio(context, user.id)
        if check_bio_links and has_personal_channel:
            await enforce_moderation(
                update,
                context,
                "Linked Channel in Bio",
                is_bio_violation=True,
            )
            return

        bio_reason = bio_has_violation(
            bio_text,
            check_links=check_bio_links,
            check_usernames=check_bio_usernames,
        )
        if bio_reason:
            await enforce_moderation(update, context, bio_reason, is_bio_violation=True)
            return


# ==========================================================
# Start, Help, & Interactive Callback Menu Handlers
# ==========================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return

    first = user.first_name or "User"
    username = user.username
    me = await context.bot.get_me()
    bot_username = me.username or "BioLinkGuardianBot"

    if chat.type == ChatType.PRIVATE:
        await db.add_or_update_user(user.id, username, first)
        user_link = (
            f"@{username}"
            if username
            else f'<a href="tg://user?id={user.id}">{first}</a>'
        )
        await send_log(
            context,
            f"📝 <b>#NEW_USER_STARTED</b>\n\n"
            f"👤 <b>User:</b> {user_link}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"🔗 <b>Username:</b> @{username or 'None'}\n"
            f"🤖 <b>Action:</b> Started the bot in private",
        )

    caption, buttons = get_welcome_content(user.id, first, bot_username)
    banner_source = (
        Config.BANNER_PATH
        if os.path.exists(Config.BANNER_PATH)
        else Config.BANNER_URL
    )

    if banner_source:
        try:
            if os.path.exists(banner_source):
                with open(banner_source, "rb") as photo_file:
                    await message.reply_photo(
                        photo=photo_file,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        has_spoiler=True,
                        reply_markup=buttons,
                    )
            else:
                await message.reply_photo(
                    photo=banner_source,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    has_spoiler=True,
                    reply_markup=buttons,
                )
            return
        except TelegramError as exc:
            logger.debug("Fallback to text welcome (banner error: %s)", exc)

    await message.reply_text(
        caption,
        parse_mode=ParseMode.HTML,
        reply_markup=buttons,
    )


async def help_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.effective_message:
        return
    help_text, buttons = get_help_content()
    await update.effective_message.reply_text(
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=buttons,
    )


async def callback_query_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    chat = update.effective_chat
    user = update.effective_user

    if data == "help_menu":
        await query.answer()
        help_text, buttons = get_help_content()
        try:
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(
                    caption=help_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=buttons,
                )
            else:
                await query.edit_message_text(
                    text=help_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=buttons,
                )
        except TelegramError:
            pass
        return

    if data == "back_start":
        await query.answer()
        first = user.first_name if user else "User"
        uid = user.id if user else 0
        me = await context.bot.get_me()
        bot_username = me.username or "BioLinkGuardianBot"
        caption, buttons = get_welcome_content(uid, first, bot_username)
        try:
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=buttons,
                )
            else:
                await query.edit_message_text(
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=buttons,
                )
        except TelegramError:
            pass
        return

    # Settings panel callbacks (Admins & Owner only)
    if data in ("toggle_bio", "toggle_link", "toggle_mention") or data.startswith("act_"):
        if not chat:
            return
        if not await is_authorized_admin(update, context):
            await query.answer("❌ Only Admins can change settings.", show_alert=True)
            return

        settings = await db.get_group_config(chat.id)
        if data == "toggle_bio":
            new_val = not bool(settings.get("bio_link_filter", True))
            await db.update_group_config(
                chat.id,
                {"bio_link_filter": new_val, "bio_username_filter": new_val},
            )
            await query.answer(f"Bio Shield: {'ON' if new_val else 'OFF'}")
        elif data == "toggle_link":
            new_val = not bool(settings.get("msg_link_filter", True))
            await db.update_group_config(chat.id, {"msg_link_filter": new_val})
            await query.answer(f"Link Shield: {'ON' if new_val else 'OFF'}")
        elif data == "toggle_mention":
            new_val = not bool(settings.get("msg_username_filter", False))
            await db.update_group_config(chat.id, {"msg_username_filter": new_val})
            await query.answer(f"@Mention Shield: {'ON' if new_val else 'OFF'}")
        elif data.startswith("act_"):
            act_mode = data.replace("act_", "")
            if act_mode in ("delete", "warn", "mute", "ban"):
                await db.update_group_config(chat.id, {"action": act_mode})
                await query.answer(f"Penalty Action set to {act_mode.upper()}")

        text, buttons = await render_settings_view(chat.id)
        try:
            await query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=buttons,
            )
        except TelegramError:
            pass


# ==========================================================
# Group Action & Admin Commands
# ==========================================================

async def my_chat_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Tracks when the bot is added to a group or its admin rights change."""
    chat = update.effective_chat
    member_update = update.my_chat_member
    if not chat or not member_update or chat.type == ChatType.PRIVATE:
        return

    new_status = member_update.new_chat_member.status
    old_status = member_update.old_chat_member.status
    adder = member_update.from_user
    chat_title = chat.title or "Group"

    if old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) and new_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    ):
        try:
            members_count = await context.bot.get_chat_member_count(chat.id)
        except TelegramError:
            members_count = 0

        await db.add_or_update_group(chat.id, chat_title, members_count)
        adder_id = adder.id if adder else 0
        adder_name = adder.first_name if adder else "Unknown"
        adder_user = adder.username if adder else None
        adder_link = (
            f"@{adder_user}"
            if adder_user
            else f'<a href="tg://user?id={adder_id}">{adder_name}</a>'
        )

        await send_log(
            context,
            f"📢 <b>#NEW_GROUP_ADDED</b>\n\n"
            f"👥 <b>Group Name:</b> <b>{chat_title}</b>\n"
            f"🆔 <b>Group ID:</b> <code>{chat.id}</code>\n"
            f"📊 <b>Members Count:</b> <code>{members_count}</code>\n"
            f"👤 <b>Added By:</b> {adder_link} (<code>{adder_id}</code>)",
        )
        await asyncio.sleep(2)
        await check_admin_and_auto_leave(context, chat.id, chat_title)
        return

    if new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        await db.remove_group(chat.id)
        return

    await check_admin_and_auto_leave(context, chat.id, chat_title)


async def settings_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not await is_authorized_admin(update, context):
        await msg.reply_text(
            "❌ Only Group Admins and the Bot Owner can access settings."
        )
        return
    text, buttons = await render_settings_view(chat.id)
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


async def biolink_toggle_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not await is_authorized_admin(update, context):
        await msg.reply_text(
            "❌ Only Group Admins and the Bot Owner can configure Bio Shield."
        )
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        settings = await db.get_group_config(chat.id)
        status = "🟢 ENABLED" if settings["bio_link_filter"] else "🔴 DISABLED"
        await msg.reply_text(
            f"🛡️ <b>Bio Link &amp; @Username Shield:</b> <code>{status}</code>\n\n"
            "• Use <code>/biolink on</code> to enable\n"
            "• Use <code>/biolink off</code> to disable",
            parse_mode=ParseMode.HTML,
        )
        return

    enabled = context.args[0].lower() == "on"
    await db.update_group_config(
        chat.id, {"bio_link_filter": enabled, "bio_username_filter": enabled}
    )
    status_str = "🟢 <b>ENABLED</b>" if enabled else "🔴 <b>DISABLED</b>"
    await msg.reply_text(
        f"✅ Bio Link &amp; @Username Shield is now {status_str} for this chat.",
        parse_mode=ParseMode.HTML,
    )


async def antilink_toggle_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not await is_authorized_admin(update, context):
        await msg.reply_text(
            "❌ Only Group Admins and the Bot Owner can configure Link Guardian."
        )
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        settings = await db.get_group_config(chat.id)
        status = "🟢 ENABLED" if settings["msg_link_filter"] else "🔴 DISABLED"
        await msg.reply_text(
            f"🚫 <b>Group Link Guardian:</b> <code>{status}</code>\n\n"
            "• Use <code>/antilink on</code> to enable\n"
            "• Use <code>/antilink off</code> to disable",
            parse_mode=ParseMode.HTML,
        )
        return

    enabled = context.args[0].lower() == "on"
    await db.update_group_config(chat.id, {"msg_link_filter": enabled})
    status_str = "🟢 <b>ENABLED</b>" if enabled else "🔴 <b>DISABLED</b>"
    await msg.reply_text(
        f"✅ Group Link Guardian (auto-deleting chat links) is now {status_str} for this chat.",
        parse_mode=ParseMode.HTML,
    )


async def antimention_toggle_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not await is_authorized_admin(update, context):
        await msg.reply_text(
            "❌ Only Group Admins and the Bot Owner can configure @Mention Shield."
        )
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        settings = await db.get_group_config(chat.id)
        status = "🟢 ENABLED" if settings["msg_username_filter"] else "🔴 DISABLED"
        await msg.reply_text(
            f"🚫 <b>Group @Username Guardian:</b> <code>{status}</code>\n\n"
            "• Use <code>/antimention on</code> to enable\n"
            "• Use <code>/antimention off</code> to disable",
            parse_mode=ParseMode.HTML,
        )
        return

    enabled = context.args[0].lower() == "on"
    await db.update_group_config(chat.id, {"msg_username_filter": enabled})
    status_str = "🟢 <b>ENABLED</b>" if enabled else "🔴 <b>DISABLED</b>"
    await msg.reply_text(
        f"✅ Group @Username Guardian is now {status_str} for this chat.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or not await is_authorized_admin(update, context):
        return
    target_id: Optional[int] = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_id = msg.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])

    if not target_id:
        await msg.reply_text(
            "ℹ️ Reply to a user's message with <code>/whitelist</code> or pass their ID: <code>/whitelist 123456789</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await db.add_whitelist_user(chat.id, target_id)
    await msg.reply_text(
        f"✅ User <code>{target_id}</code> has been whitelisted in this group.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unwhitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or not await is_authorized_admin(update, context):
        return
    target_id: Optional[int] = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_id = msg.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])

    if not target_id:
        await msg.reply_text(
            "ℹ️ Reply to a user's message with <code>/unwhitelist</code> or pass their ID: <code>/unwhitelist 123456789</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await db.remove_whitelist_user(chat.id, target_id)
    await msg.reply_text(
        f"✅ User <code>{target_id}</code> has been removed from the whitelist.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or not await is_authorized_admin(update, context):
        return
    target_id: Optional[int] = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_id = msg.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target_id = int(context.args[0])

    if not target_id:
        await msg.reply_text(
            "ℹ️ Reply to a user's message with <code>/resetwarns</code> or pass their ID.",
            parse_mode=ParseMode.HTML,
        )
        return

    await db.reset_warnings(chat.id, target_id)
    await msg.reply_text(
        f"✅ Warnings cleared for user <code>{target_id}</code>.",
        parse_mode=ParseMode.HTML,
    )


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    s = await db.get_stats()
    total_deletions = s["bio_deleted"] + s["msg_deleted"]
    text = (
        "📊 <b>Real-Time Moderation Metrics:</b>\n\n"
        f"• <b>Registered Users:</b> <code>{s['users_count']}</code>\n"
        f"• <b>Active Groups:</b> <code>{s['groups_count']}</code>\n"
        f"• <b>Total Messages Scanned:</b> <code>{s['total_scans']}</code>\n"
        f"• <b>Total Auto-Deleted:</b> <code>{total_deletions}</code>\n"
        f"• <b>Bio-Link Deletions:</b> <code>{s['bio_deleted']}</code>\n"
        f"• <b>Chat-Link Deletions:</b> <code>{s['msg_deleted']}</code>\n"
        f"• <b>Bio Cache Hits:</b> <code>{s['cache_hits']}</code>\n"
        f"• <b>Database Engine:</b> <code>MongoDB Async (Active 24/7)</code>"
    )
    await msg.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_support_buttons(),
    )


async def broadcast_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not Config.OWNER_ID or user.id != Config.OWNER_ID:
        await msg.reply_text(
            f"❌ Unauthorized: Only the Bot Owner (ID: <code>{Config.OWNER_ID}</code>) can broadcast.",
            parse_mode=ParseMode.HTML,
        )
        return

    broadcast_text = " ".join(context.args).strip() if context.args else ""
    reply_msg = msg.reply_to_message

    if not broadcast_text and not reply_msg:
        await msg.reply_text(
            "⚠️ Usage: <code>/broadcast &lt;message&gt;</code> or reply to any photo/sticker/post with <code>/broadcast</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    users = await db.get_all_users()
    groups = await db.get_all_groups()
    total_targets = len(users) + len(groups)

    status_msg = await msg.reply_text(
        f"⏳ <b>Broadcasting started...</b>\n\n"
        f"• <b>Target Users:</b> <code>{len(users)}</code>\n"
        f"• <b>Target Groups:</b> <code>{len(groups)}</code>\n"
        f"• <b>Total Targets:</b> <code>{total_targets}</code>",
        parse_mode=ParseMode.HTML,
    )

    success_users = 0
    success_groups = 0
    failed = 0

    for uid in users:
        try:
            if reply_msg:
                await reply_msg.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=broadcast_text)
            success_users += 1
            await asyncio.sleep(0.04)
        except TelegramError:
            failed += 1

    for g in groups:
        gid = g.get("chat_id")
        if not gid:
            continue
        try:
            if reply_msg:
                await reply_msg.copy(chat_id=gid)
            else:
                await context.bot.send_message(chat_id=gid, text=broadcast_text)
            success_groups += 1
            await asyncio.sleep(0.04)
        except TelegramError:
            failed += 1

    await status_msg.edit_text(
        "✅ <b>Broadcast Completed!</b>\n\n"
        f"👤 <b>Users Reached:</b> <code>{success_users}/{len(users)}</code>\n"
        f"👥 <b>Groups Reached:</b> <code>{success_groups}/{len(groups)}</code>\n"
        f"⚠️ <b>Failed / Blocked:</b> <code>{failed}</code>",
        parse_mode=ParseMode.HTML,
    )


# ==========================================================
# Render Health-Check HTTP Server & Bot Runner
# ==========================================================

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {"status": "ok", "service": "telegram-biolink-guardian-bot"}
    )


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    logger.info("Health-check HTTP server listening on port %s", Config.PORT)
    return runner


async def main() -> None:
    if not Config.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing! Set it in .env or Render Dashboard."
        )

    await db.connect()
    health_runner = await start_health_server()

    application = Application.builder().token(Config.BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_command_handler))
    application.add_handler(CommandHandler("settings", settings_command_handler))
    application.add_handler(CommandHandler("biolink", biolink_toggle_handler))
    application.add_handler(CommandHandler("antilink", antilink_toggle_handler))
    application.add_handler(CommandHandler("antimention", antimention_toggle_handler))
    application.add_handler(CommandHandler("whitelist", cmd_whitelist))
    application.add_handler(CommandHandler("unwhitelist", cmd_unwhitelist))
    application.add_handler(CommandHandler("resetwarns", cmd_resetwarns))
    application.add_handler(CommandHandler("stats", stats_handler))
    application.add_handler(CommandHandler("broadcast", broadcast_handler))

    # Register interactive button callbacks & group member status handlers
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(
        ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # Register group message handler (new + edited messages)
    group_filter = filters.ChatType.GROUPS & (~filters.COMMAND)
    application.add_handler(MessageHandler(group_filter, handle_group_message))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    logger.info("Telegram Bio & Group Link Guardian Bot is now running!")

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down bot...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
