import os
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)


class Base(DeclarativeBase):
    pass


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    agency_name: Mapped[str] = mapped_column(String, default="")
    hardware_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # привязывается при активации
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_check: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


def init_db():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def create_license(key: str, agency_name: str = "", notes: str = "") -> License:
    with get_session() as s:
        lic = License(key=key, agency_name=agency_name, notes=notes)
        s.add(lic)
        s.commit()
        s.refresh(lic)
        return lic


def get_license(key: str) -> Optional[License]:
    with get_session() as s:
        return s.scalar(select(License).where(License.key == key))


def get_all_licenses() -> list[License]:
    with get_session() as s:
        return list(s.scalars(select(License).order_by(License.created_at.desc())))


def activate_license(key: str, hardware_id: str) -> Optional[License]:
    with get_session() as s:
        lic = s.scalar(select(License).where(License.key == key))
        if not lic:
            return None
        if not lic.hardware_id:
            # Первая активация — привязываем к железу
            lic.hardware_id = hardware_id
            lic.activated_at = datetime.utcnow()
        lic.last_check = datetime.utcnow()
        s.commit()
        s.refresh(lic)
        return lic


def update_last_check(key: str):
    with get_session() as s:
        s.execute(update(License).where(License.key == key).values(last_check=datetime.utcnow()))
        s.commit()


def revoke_license(key: str):
    with get_session() as s:
        s.execute(update(License).where(License.key == key).values(is_active=False))
        s.commit()


def enable_license(key: str):
    with get_session() as s:
        s.execute(update(License).where(License.key == key).values(is_active=True))
        s.commit()


def delete_license(key: str):
    with get_session() as s:
        lic = s.scalar(select(License).where(License.key == key))
        if lic:
            s.delete(lic)
            s.commit()
