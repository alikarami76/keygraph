"""SQLAlchemy models for the fictional shop (never run; keygraph reads this file as text)."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    email = Column(String(190))
    first_name = Column(String(80))
    status = Column(Integer)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    customer_id = Column(ForeignKey("customers.id"))
    status = Column(Integer)
    total = Column(Numeric(12, 2))
    placed_at = Column("placed_at", DateTime)
