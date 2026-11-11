# Telegram Bio & Group Link Guardian Bot (Render + MongoDB)

A Telegram group moderation bot built with [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot) and async [MongoDB Atlas](https://www.mongodb.com/atlas) (`motor`), featuring an interactive inline button UI, spoiler banner welcome screen, live `/settings` control panel, audit log channel notifications, and one-click [Render](https://render.com) deployment.

## Key Features & Interactive UI

- **Interactive Welcome UI & Spoiler Banner (`/start`)**: Greets users with an HTML `<blockquote>` card, optional spoiler photo banner (`BANNER_URL` or `BANNER_PATH`), and inline buttons (`Add me to your group`, `❓ Help and commands`, `Updates 📢`, `Support 💬`).
- **Interactive Group Protection Control Panel (`/settings`)**: Inline toggle buttons for **Bio Shield** (`🟢 ON` / `🔴 OFF`), **Link Shield** (`🟢 ON` / `🔴 OFF`), **@Mention Shield** (`🟢 ON` / `🔴 OFF`), and **Penalty Action Mode** (`Delete`, `Warn`, `Mute`, `Ban`).
- **Bio Link & `@Username` Auto-Deletion**: Automatically deletes group messages from any non-admin member whose Telegram profile bio or linked channel contains a link or `@username`.
- **Group Message Link & `@Username` Auto-Deletion**: Automatically removes URLs, hidden text links, and promotional `@username` mentions sent or edited in the group chat.
- **Audit Log Group & Auto-Leave Protection**: Sends `#NEW_USER_STARTED`, `#NEW_GROUP_ADDED`, and `#AUTO_LEFT_GROUP` logs to `LOG_GROUP_ID`, and automatically leaves groups if administrator permissions are missing or revoked.
- **Owner Broadcast (`/broadcast`) & Live Metrics (`/stats`)**: Broadcast text or media to all registered MongoDB users and groups.

## Environment Variables (`.env`)

Copy `.env.example` to `.env` for local testing, or configure these keys in your [Render Dashboard](https://render.com):

| Variable | Description | Example |
| :--- | :--- | :--- |
| `BOT_TOKEN` | Telegram Bot token from [@BotFather](https://t.me/BotFather) | `123456:ABC-DEF...` |
| `MONGO_URI` | MongoDB connection string from [MongoDB Atlas](https://www.mongodb.com/atlas) | `mongodb+srv://user:pass@cluster...` |
| `DATABASE_NAME` | MongoDB database name | `biolink_guardian_bot` |
| `OWNER_ID` | Bot owner numeric Telegram User ID (for `/broadcast` & admin bypass) | `8677818347` |
| `LOG_GROUP_ID` | Telegram Group/Channel ID for audit logs (`0` to disable) | `-1001234567890` |
| `SUPPORT_CHANNEL_URL` | URL for the `Updates 📢` and `Support 💬` inline buttons | `https://t.me/Telegram` |
| `BANNER_URL` | Direct image URL for the `/start` spoiler welcome photo | `https://...` |
| `DEFAULT_ACTION` | Default moderation penalty (`delete`, `warn`, `mute`, `ban`) | `delete` |
| `PORT` | Health-check HTTP port (injected automatically by [Render](https://render.com)) | `8080` |
