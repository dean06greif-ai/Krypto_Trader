"""Einmaliges Aufräumen von Testdaten (MasterPrompt zurücksetzen, Test-Kandidat entfernen)."""
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    res = await db.ai_strategy_candidates.delete_many({"name": "Test Kandidat"})
    await db.settings.delete_one({"_id": "ai_master_prompt"})
    print("candidates removed:", res.deleted_count)


asyncio.run(main())
