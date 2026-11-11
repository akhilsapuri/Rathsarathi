from database import Base
from sqlalchemy import Column, Integer, String, Float
from database import Base
class Ride(Base):
    __tablename__ = "rides"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    pickup_location = Column(String)
    destination = Column(String)
status = Column(String, default="pending")

