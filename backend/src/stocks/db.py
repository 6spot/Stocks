from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from stocks.settings import settings

@lru_cache
def session_factory():
    return sessionmaker(bind=create_engine(settings().database_url, pool_pre_ping=True), expire_on_commit=False)
