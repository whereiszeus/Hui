import logging
from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self) -> None:
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self._mem_groups: Dict[int, Dict[str, Any]] = {}
        self._mem_users: Dict[int, Dict[str, Any]] = {}
        self._mem_stats: Dict[str, int] = {
            "total_scans": 0,
            "bio_deleted": 0,
            "msg_deleted": 0,
            "cache_hits": 0,
        }

    async def connect(self) -> None:
        if not Config.MONGO_URI:
            logger.warning("MONGO_URI is not set. Running with in-memory fallback.")
            return
        self.client = AsyncIOMotorClient(Config.MONGO_URI)
        self.db = self.client[Config.DATABASE_NAME]
        await self.db.groups.create_index("chat_id", unique=True)
        await self.db.users.create_index("user_id", unique=True)
        await self.db.warnings.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
        logger.info("Connected to MongoDB database: %s", Config.DATABASE_NAME)

    def default_group_config(self, chat_id: int, title: str = "Group", members_count: int = 0) -> Dict[str, Any]:
        return {
            "chat_id": chat_id,
            "title": title,
            "members_count": members_count,
            "bio_link_filter": True,
            "bio_username_filter": True,
            "msg_link_filter": True,
            "msg_username_filter": False,
            "action": Config.DEFAULT_ACTION,
            "max_warnings": Config.MAX_WARNINGS,
            "mute_minutes": Config.MUTE_DURATION_MINUTES,
            "whitelisted_users": [],
        }

    async def add_or_update_user(self, user_id: int, username: Optional[str], first_name: str) -> None:
        doc = {"user_id": user_id, "username": username, "first_name": first_name}
        self._mem_users[user_id] = doc
        if self.db is not None:
            await self.db.users.update_one({"user_id": user_id}, {"$set": doc}, upsert=True)

    async def get_all_users(self) -> List[int]:
        if self.db is None:
            return list(self._mem_users.keys())
        cursor = self.db.users.find({}, {"user_id": 1})
        docs = await cursor.to_list(length=100000)
        return [int(d["user_id"]) for d in docs if "user_id" in d]

    async def add_or_update_group(self, chat_id: int, title: str, members_count: int = 0) -> None:
        if self.db is None:
            cfg = self._mem_groups.get(chat_id) or self.default_group_config(chat_id, title, members_count)
            cfg["title"] = title
            cfg["members_count"] = members_count
            self._mem_groups[chat_id] = cfg
            return
        existing = await self.db.groups.find_one({"chat_id": chat_id})
        if not existing:
            await self.db.groups.insert_one(self.default_group_config(chat_id, title, members_count))
        else:
            await self.db.groups.update_one(
                {"chat_id": chat_id},
                {"$set": {"title": title, "members_count": members_count}},
            )

    async def remove_group(self, chat_id: int) -> None:
        self._mem_groups.pop(chat_id, None)
        if self.db is not None:
            await self.db.groups.delete_one({"chat_id": chat_id})

    async def get_all_groups(self) -> List[Dict[str, Any]]:
        if self.db is None:
            return list(self._mem_groups.values())
        cursor = self.db.groups.find({})
        return await cursor.to_list(length=50000)

    async def get_group_config(self, chat_id: int) -> Dict[str, Any]:
        defaults = self.default_group_config(chat_id)
        if self.db is None:
            if chat_id not in self._mem_groups:
                self._mem_groups[chat_id] = defaults.copy()
            return self._mem_groups[chat_id]
        doc = await self.db.groups.find_one({"chat_id": chat_id})
        if not doc:
            await self.db.groups.insert_one(defaults.copy())
            return defaults
        return {**defaults, **doc}

    async def update_group_config(self, chat_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        if self.db is None:
            cfg = await self.get_group_config(chat_id)
            cfg.update(updates)
            self._mem_groups[chat_id] = cfg
            return cfg
        await self.db.groups.update_one(
            {"chat_id": chat_id},
            {"$set": updates},
            upsert=True,
        )
        return await self.get_group_config(chat_id)

    async def add_whitelist_user(self, chat_id: int, user_id: int) -> None:
        if self.db is None:
            cfg = await self.get_group_config(chat_id)
            wl = set(cfg.get("whitelisted_users", []))
            wl.add(user_id)
            cfg["whitelisted_users"] = list(wl)
            return
        await self.db.groups.update_one(
            {"chat_id": chat_id},
            {"$addToSet": {"whitelisted_users": user_id}},
            upsert=True,
        )

    async def remove_whitelist_user(self, chat_id: int, user_id: int) -> None:
        if self.db is None:
            cfg = await self.get_group_config(chat_id)
            wl = [u for u in cfg.get("whitelisted_users", []) if u != user_id]
            cfg["whitelisted_users"] = wl
            return
        await self.db.groups.update_one(
            {"chat_id": chat_id},
            {"$pull": {"whitelisted_users": user_id}},
        )

    async def get_whitelisted_users(self, chat_id: int) -> List[int]:
        cfg = await self.get_group_config(chat_id)
        return list(cfg.get("whitelisted_users", []))

    async def increment_warning(self, chat_id: int, user_id: int, reason: str) -> int:
        if self.db is None:
            return 1
        result = await self.db.warnings.find_one_and_update(
            {"chat_id": chat_id, "user_id": user_id},
            {"$inc": {"count": 1}, "$push": {"reasons": reason}},
            upsert=True,
            return_document=True,
        )
        return int(result.get("count", 1))

    async def reset_warnings(self, chat_id: int, user_id: int) -> None:
        if self.db is not None:
            await self.db.warnings.delete_one({"chat_id": chat_id, "user_id": user_id})

    async def increment_stat(self, field: str) -> None:
        self._mem_stats[field] = self._mem_stats.get(field, 0) + 1
        if self.db is not None:
            await self.db.stats.update_one(
                {"_id": "global"},
                {"$inc": {field: 1}},
                upsert=True,
            )

    async def get_stats(self) -> Dict[str, int]:
        if self.db is None:
            return {
                "total_scans": self._mem_stats.get("total_scans", 0),
                "bio_deleted": self._mem_stats.get("bio_deleted", 0),
                "msg_deleted": self._mem_stats.get("msg_deleted", 0),
                "cache_hits": self._mem_stats.get("cache_hits", 0),
                "users_count": len(self._mem_users),
                "groups_count": len(self._mem_groups),
            }
        stat_doc = await self.db.stats.find_one({"_id": "global"}) or {}
        groups_count = await self.db.groups.count_documents({})
        users_count = await self.db.users.count_documents({})
        return {
            "total_scans": int(stat_doc.get("total_scans", 0)),
            "bio_deleted": int(stat_doc.get("bio_deleted", 0)),
            "msg_deleted": int(stat_doc.get("msg_deleted", 0)),
            "cache_hits": int(stat_doc.get("cache_hits", 0)),
            "users_count": int(users_count),
            "groups_count": int(groups_count),
        }


db = Database()
