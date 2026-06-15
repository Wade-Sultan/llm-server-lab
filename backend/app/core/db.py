from collections.abc import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models import User
from app.schemas import UserCreate


def _create_engine():
    if settings.CLOUD_SQL_INSTANCE:
        from google.cloud.sql.connector import Connector, IPTypes

        connector = Connector()

        def getconn():
            return connector.connect(
                settings.CLOUD_SQL_INSTANCE,
                "pg8000",
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                db=settings.POSTGRES_DB,
                ip_type=IPTypes.PUBLIC,
            )

        return create_engine(
            "postgresql+pg8000://", 
            creator=getconn,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

    return create_engine(
        str(settings.SQLALCHEMY_DATABASE_URI),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


engine = _create_engine()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(session: Session) -> None:
    # Lazy import to avoid circular dependency:
    # db.py → crud → crud/users → core/security → core/config → Settings()
    from app import crud

    user = session.execute(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).scalar_one_or_none()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        crud.create_user(session=session, user_create=user_in)

