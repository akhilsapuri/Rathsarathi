from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
import math
import socketio

sio = socketio.AsyncServer(async_mode='asgi')
app = FastAPI()
app.mount("/", socketio.ASGIApp(sio, app))

rides = {}

# Dummy driver data with locations
# Ikkada dummy driver data and locations unnay
drivers = [
    {"driver_id": "d1", "lat": 17.730, "lon": 83.200},
    {"driver_id": "d2", "lat": 17.740, "lon": 83.210},
]

class RideRequest(BaseModel):
    user_id: str
    pickup_location: str
    destination: str
    vehicle_type: str
    user_lat: float
    user_lon: float

def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.post("/create_ride_request/")
async def create_ride_request(ride: RideRequest):
    available_drivers = []
    
    # Driver matching logic for 500m to 2km
    # 500m nundi 2km range kosam driver matching logic
    for driver in drivers:
        distance = calculate_haversine_distance(ride.user_lat, ride.user_lon, driver["lat"], driver["lon"])
        if 0.5 <= distance <= 2.0:
            available_drivers.append(driver["driver_id"])

    if not available_drivers:
        raise HTTPException(status_code=404, detail="No drivers available in the range")

    ride_id = str(uuid.uuid4())
    rides[ride_id] = {
        "user_id": ride.user_id,
        "pickup_location": ride.pickup_location,
        "destination": ride.destination,
        "vehicle_type": ride.vehicle_type,
        "status": "pending",
        "matched_drivers": available_drivers
    }

    await sio.emit('ride_requested', {'ride_id': ride_id, 'status': 'pending', 'drivers': available_drivers})
    
    return {"ride_id": ride_id, "status": "Ride request created successfully", "matched_drivers": available_drivers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)