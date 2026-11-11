import re
from typing import Optional
from telegram import Message, MessageEntity

# Matches standard URLs, Telegram links (t.me, telegram.me, telegram.dog),
# bio-link shorteners, and bare domain links (e.g., example.com/path)
URL_REGEX = re.compile(
    r"(?i)\b("
    r"(?:https?://|www\.|t\.me/|telegram\.(?:me|dog)/|bio\.link/|linktr\.ee/)\S+"
    r"|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|org|net|in|io|me|co|xyz|info|biz|gg|cc|ly|to|dev|app|link|site|online|store|shop|club|vip|pro|top|icu|live|tech)"
    r"(?:/\S*)?"
    r")"
)

# Matches @username mentions (at least 4 characters after @, per Telegram rules)
USERNAME_REGEX = re.compile(r"(?<![a-zA-Z0-9_])@[a-zA-Z][a-zA-Z0-9_]{3,31}\b")


def text_contains_link(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(URL_REGEX.search(text))


def text_contains_username(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(USERNAME_REGEX.search(text))


def bio_has_violation(
    bio: Optional[str],
    check_links: bool = True,
    check_usernames: bool = True,
) -> Optional[str]:
    """
    Inspects a user's Telegram bio text for links or @username mentions.
    Returns the violation reason string if matched, otherwise None.
    """
    if not bio:
        return None
    if check_links and text_contains_link(bio):
        return "Link in Bio"
    if check_usernames and text_contains_username(bio):
        return "@Username in Bio"
    return None


def message_has_violation(
    message: Message,
    check_links: bool = True,
    check_usernames: bool = True,
) -> Optional[str]:
    """
    Inspects a group message (text, caption, and formatting entities)
    for URLs, hidden text links, or @username promotions.
    Returns the violation reason string if matched, otherwise None.
    """
    content = (message.text or "") + "\n" + (message.caption or "")
    entities = list(message.entities or []) + list(message.caption_entities or [])

    if check_links:
        for ent in entities:
            if ent.type in (MessageEntity.URL, MessageEntity.TEXT_LINK):
                return "Link in Group Message"
        if text_contains_link(content):
            return "Link in Group Message"

    if check_usernames:
        for ent in entities:
            if ent.type in (MessageEntity.MENTION, MessageEntity.TEXT_MENTION):
                return "@Username in Group Message"
        if text_contains_username(content):
            return "@Username in Group Message"

    return None
