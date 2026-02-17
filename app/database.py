from sqlmodel import create_engine, Session
from app.config import settings

# echo=True ensures we see SQL queries in the logs, useful for development
engine = create_engine(settings.DATABASE_URL, echo=True)

def get_session():
    with Session(engine) as session:
        yield session
