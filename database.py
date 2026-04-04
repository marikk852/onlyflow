import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text,
    create_engine, select, update, delete
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

DB_PATH = Path("contentflow.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )
    batches: Mapped[list["ContentBatch"]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, default="")
    session_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    forum_topic_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    model: Mapped["Model"] = relationship(back_populates="accounts")
    galleries: Mapped[list["Gallery"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    upload_jobs: Mapped[list["UploadJob"]] = relationship(back_populates="account")
    upload_history: Mapped[list["UploadHistory"]] = relationship(back_populates="account")


class Gallery(Base):
    __tablename__ = "galleries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)

    account: Mapped["Account"] = relationship(back_populates="galleries")
    upload_jobs: Mapped[list["UploadJob"]] = relationship(back_populates="gallery")
    upload_history: Mapped[list["UploadHistory"]] = relationship(back_populates="gallery")


class ContentBatch(Base):
    __tablename__ = "content_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    telegram_msg_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    file_paths: Mapped[str] = mapped_column(Text, default="[]")        # JSON
    file_hashes: Mapped[str] = mapped_column(Text, default="[]")       # JSON
    status: Mapped[str] = mapped_column(String, default="pending")     # pending/approved/uploading/done/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    model: Mapped["Model"] = relationship(back_populates="batches")
    upload_jobs: Mapped[list["UploadJob"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("content_batches.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    gallery_id: Mapped[int] = mapped_column(ForeignKey("galleries.id"))
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/running/done/error
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    files_count: Mapped[int] = mapped_column(Integer, default=0)

    batch: Mapped["ContentBatch"] = relationship(back_populates="upload_jobs")
    account: Mapped["Account"] = relationship(back_populates="upload_jobs")
    gallery: Mapped["Gallery"] = relationship(back_populates="upload_jobs")


class UploadHistory(Base):
    __tablename__ = "upload_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    gallery_id: Mapped[int] = mapped_column(ForeignKey("galleries.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    batch_id: Mapped[int] = mapped_column(ForeignKey("content_batches.id"))

    account: Mapped["Account"] = relationship(back_populates="upload_history")
    gallery: Mapped["Gallery"] = relationship(back_populates="upload_history")


def init_db():
    Base.metadata.create_all(engine)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_session() -> Session:
    return Session(engine)


# Models

def add_model(name: str, alias: str = "") -> Model:
    with get_session() as s:
        m = Model(name=name, alias=alias)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m

def get_model_by_name(name: str) -> Optional[Model]:
    with get_session() as s:
        return s.scalar(select(Model).where(Model.name.ilike(name)))

def get_model_by_id(model_id: int) -> Optional[Model]:
    with get_session() as s:
        return s.get(Model, model_id)

def get_all_models() -> list[Model]:
    with get_session() as s:
        return list(s.scalars(select(Model).order_by(Model.id)))

def delete_model(model_id: int):
    with get_session() as s:
        s.execute(delete(Model).where(Model.id == model_id))
        s.commit()

def set_model_forum_topic(model_id: int, forum_topic_id: int):
    with get_session() as s:
        s.execute(
            update(Account)
            .where(Account.model_id == model_id)
            .values(forum_topic_id=None)
        )
        s.commit()

def get_model_by_forum_topic(forum_topic_id: int) -> Optional[Model]:
    with get_session() as s:
        account = s.scalar(
            select(Account).where(Account.forum_topic_id == forum_topic_id)
        )
        if account:
            return s.get(Model, account.model_id)
        return None


# Accounts

def add_account(model_id: int, name: str, url: str = "") -> Account:
    with get_session() as s:
        a = Account(model_id=model_id, name=name, url=url)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a

def get_account(account_id: int) -> Optional[Account]:
    with get_session() as s:
        return s.get(Account, account_id)

def get_accounts_by_model(model_id: int) -> list[Account]:
    with get_session() as s:
        return list(s.scalars(select(Account).where(Account.model_id == model_id).order_by(Account.id)))

def set_session_ok(account_id: int, ok: bool):
    with get_session() as s:
        s.execute(update(Account).where(Account.id == account_id).values(session_ok=ok))
        s.commit()

def set_account_forum_topic(account_id: int, forum_topic_id: int):
    with get_session() as s:
        s.execute(update(Account).where(Account.id == account_id).values(forum_topic_id=forum_topic_id))
        s.commit()

def delete_account(account_id: int):
    with get_session() as s:
        s.execute(delete(Account).where(Account.id == account_id))
        s.commit()

def get_all_accounts() -> list[Account]:
    with get_session() as s:
        return list(s.scalars(select(Account).order_by(Account.model_id, Account.id)))


# Galleries

def add_gallery(account_id: int, name: str) -> Gallery:
    with get_session() as s:
        g = Gallery(account_id=account_id, name=name)
        s.add(g)
        s.commit()
        s.refresh(g)
        return g

def get_galleries(account_id: int) -> list[Gallery]:
    with get_session() as s:
        return list(s.scalars(select(Gallery).where(Gallery.account_id == account_id).order_by(Gallery.id)))

def get_gallery(gallery_id: int) -> Optional[Gallery]:
    with get_session() as s:
        return s.get(Gallery, gallery_id)

def delete_gallery(gallery_id: int):
    with get_session() as s:
        s.execute(delete(Gallery).where(Gallery.id == gallery_id))
        s.commit()


# Content Batches

def create_batch(model_id: int, msg_ids: list, file_paths: list, file_hashes: list) -> ContentBatch:
    with get_session() as s:
        b = ContentBatch(
            model_id=model_id,
            telegram_msg_ids=json.dumps(msg_ids),
            file_paths=json.dumps(file_paths),
            file_hashes=json.dumps(file_hashes),
        )
        s.add(b)
        s.commit()
        s.refresh(b)
        return b

def get_batch(batch_id: int) -> Optional[ContentBatch]:
    with get_session() as s:
        return s.get(ContentBatch, batch_id)

def update_batch_status(batch_id: int, status: str, approved_at: Optional[datetime] = None):
    with get_session() as s:
        vals = {"status": status}
        if approved_at:
            vals["approved_at"] = approved_at
        s.execute(update(ContentBatch).where(ContentBatch.id == batch_id).values(**vals))
        s.commit()


# Upload Jobs

def create_upload_job(batch_id: int, account_id: int, gallery_id: int, files_count: int) -> UploadJob:
    with get_session() as s:
        j = UploadJob(batch_id=batch_id, account_id=account_id, gallery_id=gallery_id, files_count=files_count)
        s.add(j)
        s.commit()
        s.refresh(j)
        return j

def update_job_status(job_id: int, status: str, error_msg: str = None,
                      started_at: datetime = None, finished_at: datetime = None):
    with get_session() as s:
        vals = {"status": status}
        if error_msg is not None:
            vals["error_msg"] = error_msg
        if started_at:
            vals["started_at"] = started_at
        if finished_at:
            vals["finished_at"] = finished_at
        s.execute(update(UploadJob).where(UploadJob.id == job_id).values(**vals))
        s.commit()

def get_pending_jobs(batch_id: int) -> list[UploadJob]:
    with get_session() as s:
        return list(s.scalars(
            select(UploadJob).where(
                UploadJob.batch_id == batch_id,
                UploadJob.status == "pending"
            )
        ))

def get_queue() -> list[UploadJob]:
    with get_session() as s:
        return list(s.scalars(
            select(UploadJob).where(
                UploadJob.status.in_(["pending", "running"])
            ).order_by(UploadJob.id)
        ))


# Upload History

def add_history(file_hash: str, account_id: int, gallery_id: int, batch_id: int):
    with get_session() as s:
        h = UploadHistory(
            file_hash=file_hash,
            account_id=account_id,
            gallery_id=gallery_id,
            batch_id=batch_id,
        )
        s.add(h)
        s.commit()

def check_duplicates(file_hashes: list[str]) -> list[dict]:
    """Возвращает список уже загруженных файлов с деталями."""
    duplicates = []
    with get_session() as s:
        for fh in file_hashes:
            rows = list(s.scalars(
                select(UploadHistory).where(UploadHistory.file_hash == fh).order_by(UploadHistory.uploaded_at.desc())
            ))
            if rows:
                row = rows[0]
                account = s.get(Account, row.account_id)
                gallery = s.get(Gallery, row.gallery_id)
                duplicates.append({
                    "hash": fh,
                    "account_name": account.name if account else "?",
                    "gallery_name": gallery.name if gallery else "?",
                    "uploaded_at": row.uploaded_at,
                })
    return duplicates

def get_last_history(limit: int = 20) -> list[UploadHistory]:
    with get_session() as s:
        return list(s.scalars(
            select(UploadHistory).order_by(UploadHistory.uploaded_at.desc()).limit(limit)
        ))
