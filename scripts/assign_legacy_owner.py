"""Explicit local administrator assignment of preserved pre-authentication records."""

import argparse
import asyncio

from sqlalchemy import exists, func, select, update

from backend.db.models import DebugSession, KnowledgeSource, SavedSolution
from backend.db.session import close_database, get_session_factory
from backend.services.access import LEGACY_OWNER, USER_ID


async def assign_legacy_owner(owner: str, *, apply: bool = False) -> dict[str, int]:
    if not USER_ID.fullmatch(owner):
        raise ValueError("Provide a verified Clerk user ID beginning with user_")
    try:
        async with get_session_factory().begin() as db:
            hashes = select(KnowledgeSource.file_hash).where(KnowledgeSource.owner_id == LEGACY_OWNER)
            conflict = await db.scalar(
                select(exists().where(KnowledgeSource.owner_id == owner, KnowledgeSource.file_hash.in_(hashes)))
            )
            if conflict:
                raise ValueError("Assignment would duplicate an existing account source; review the records first")
            counts = {}
            for model in (KnowledgeSource, DebugSession, SavedSolution):
                counts[model.__tablename__] = int(
                    await db.scalar(select(func.count()).select_from(model).where(model.owner_id == LEGACY_OWNER)) or 0
                )
                if apply:
                    await db.execute(update(model).where(model.owner_id == LEGACY_OWNER).values(owner_id=owner))
            return counts
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Verified destination Clerk user ID")
    parser.add_argument("--apply", action="store_true", help="Commit assignment; default is a count-only preview")
    args = parser.parse_args()
    counts = asyncio.run(assign_legacy_owner(args.owner, apply=args.apply))
    print({"applied": args.apply, "records": counts})


if __name__ == "__main__":
    main()
