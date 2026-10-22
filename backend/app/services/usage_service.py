# AIMETA P=使用统计服务_API调用统计|R=统计记录_限额检查|NR=不含数据访问|E=UsageService|X=internal|A=服务类|D=sqlalchemy|S=db|RD=./README.ai
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..repositories.usage_metric_repository import UsageMetricRepository
from ..models.usage_metric import UsageMetric
from ..models.user_daily_request import UserDailyRequest


class UsageService:
    """通用计数服务，目前用于统计 API 请求次数等。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = UsageMetricRepository(session)

    async def increment(self, key: str, amount: int = 1, quota=None) -> None:
        if amount <= 0:
            return
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(UsageMetric).values(key=key, value=amount).on_conflict_do_update(
                index_elements=["key"], set_={"value": UsageMetric.value + amount})
        elif dialect == "mysql":
            statement = mysql_insert(UsageMetric).values(key=key, value=amount).on_duplicate_key_update(value=UsageMetric.value + amount)
        else:
            raise RuntimeError("用量统计仅支持已配置的 SQLite/MySQL")
        await self.session.execute(statement)
        if quota and amount > 1:
            # 已在解析配置时预留一次，剩余完成调用归入同一个起始日。
            await self.session.execute(update(UserDailyRequest).where(
                UserDailyRequest.user_id == quota["user_id"],
                UserDailyRequest.request_date == date.fromisoformat(quota["date"]),
            ).values(request_count=UserDailyRequest.request_count + amount - 1))
        await self.session.commit()

    async def get_value(self, key: str) -> int:
        value = await self.session.scalar(select(UsageMetric.value).where(UsageMetric.key == key))
        return value or 0

    async def refund_reservation(self, quota) -> None:
        """任务零次完成调用时原子退还预留；不影响同日其他任务的计数。"""
        await self.session.execute(update(UserDailyRequest).where(
            UserDailyRequest.user_id == quota["user_id"],
            UserDailyRequest.request_date == date.fromisoformat(quota["date"]),
            UserDailyRequest.request_count > 0,
        ).values(request_count=UserDailyRequest.request_count - 1))
        await self.session.commit()
