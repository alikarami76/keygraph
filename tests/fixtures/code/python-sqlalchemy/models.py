from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()
metadata = MetaData()


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    full_name = Column("name", String(80))
    email: Mapped[str] = mapped_column(String(190))


class Mixin:
    status = Column(Integer)


products = Table(
    "products",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sku", String(60)),
)
