"""Permission filters belong in database queries, before any private content is read."""

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from backend.db.models import ConnectorAccount, KnowledgeSource


def accessible_source(*, current_time: bool = False) -> ColumnElement[bool]:
    return and_(
        KnowledgeSource.is_active.is_(True),
        or_(
            KnowledgeSource.access_expires_at.is_(None),
            KnowledgeSource.access_expires_at > (func.clock_timestamp() if current_time else func.now()),
        ),
        or_(
            KnowledgeSource.connector_account_id.is_(None),
            exists(
                select(ConnectorAccount.id).where(
                    ConnectorAccount.id == KnowledgeSource.connector_account_id,
                    ConnectorAccount.owner_id == KnowledgeSource.owner_id,
                    ConnectorAccount.status.in_(("connected", "syncing", "connected_with_warning")),
                    ConnectorAccount.authentication_status == "valid",
                )
            ),
        ),
    )
