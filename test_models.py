from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship, DeclarativeBase
from datetime import datetime

print("Imports OK")

class Base(DeclarativeBase):
    pass

print("Base OK")

class Coin(Base):
    __tablename__ = "coins"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True, index=True)
    
print("Coin OK")

class TweetRaw(Base):
    __tablename__ = "tweets_raw"
    id = Column(Integer, primary_key=True)
    tweet_id = Column(String, unique=True, index=True)

print("TweetRaw OK")
