import os
from database import SessionLocal, engine, Base
Base.metadata.create_all(bind=engine)
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import requests
from requests_oauthlib import OAuth2Session
from models import Ride
import math
import uuid
import socketio
import uvicorn
import asyncio
import redis.asyncio as redis
from google import genai
import httpx
from google.genai import types


sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def read_root():
    return {"message": "Success"}

def analyze_pothole(image_path: str):
    client = genai.Client()
    with open(image_path, 'rb') as f:
        image_bytes = f.read()

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'), "Analyze this pothole."]

    )
    return response.text



app.mount("/socket.io", socketio.ASGIApp(sio))

drivers = {
    "Driver_Id1": {"lat": 17.730, "lon": 83.200},
    "Driver_Id2": {"lat": 17.740, "lon": 83.210}
}
rides = {}
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"
AUTHORIZATION_BASE_URL = "https://api.digitallocker.gov.in/authorize"
TOKEN_URL = "https://api.digitallocker.gov.in/token"
REDIRECT_URI = "http://localhost:8000/callback"

@app.get("/login")
def login():
    digilocker = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI)
    authorization_url, state = digilocker.authorization_url(AUTHORIZATION_BASE_URL)
    return {"url": authorization_url}

@app.get("/callback")
def callback(code: str, state: str):
    digilocker = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI)
    try:
        token = digilocker.fetch_token(
            TOKEN_URL,
            client_secret=CLIENT_SECRET,
            authorization_response=f"http://localhost:8000/callback?code={code}&state={state}"
        )
        return {"access_token": token}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



class RideRequest(BaseModel):
    user_id: str
    pickup_location: str
    destination: str
    vehicle_type: str
    user_lat: float
    user_lon: float


class AcceptRideRequest(BaseModel):
    ride_id: str
    driver_id: str


def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        (math.sin(dlat / 2) ** 2)
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * (math.sin(dlon / 2) ** 2)
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def send_ride_notifications(ride_id: str):
    while ride_id in rides and rides[ride_id].get("status") in ["pending", "searching"]:
        await sio.emit("ride_requested", {"ride_id": ride_id})
        await asyncio.sleep(10)


@app.post("/create_ride_request/")
async def create_ride_request(ride: RideRequest, background_tasks: BackgroundTasks):
    available_drivers = [
        d_id for d_id, d in drivers.items()
        if calculate_haversine_distance(ride.user_lat, ride.user_lon, d['lat'], d['lon']) <= 3.0
    ]
    
    if not available_drivers:
        available_drivers = [
            d_id for d_id, d in drivers.items()
            if calculate_haversine_distance(ride.user_lat, ride.user_lon, d['lat'], d['lon']) <= 5.0
        ]
        ride_id = str(uuid.uuid4())
        ride_data = ride.model_dump() if hasattr(ride, "model_dump") else ride.dict()
        rides[ride_id] = {**ride_data, "status": "pending", "matched_drivers": available_drivers}
        background_tasks.add_task(send_ride_notifications, ride_id)
        return {"ride_id": ride_id, "status": "Created", "matched_drivers": available_drivers}



@app.post("/accept_ride/")
async def accept_ride_endpoint(data: AcceptRideRequest):
    if data.ride_id not in rides:
        raise HTTPException(status_code=404, detail="Not found")
    if data.driver_id not in rides[data.ride_id]["matched_drivers"]:
        raise HTTPException(status_code=403, detail="Driver not matched")
    
    lock_key = f"lock:ride:{data.ride_id}"
    try:
        if not await redis_client.set(lock_key, data.driver_id, nx=True, ex=10):
            raise HTTPException(status_code=409, detail="Processing by another")
        if rides[data.ride_id].get("status") == "accepted":
            raise HTTPException(status_code=409, detail="Already accepted")
        
        rides[data.ride_id].update({"status": "accepted", "accepted_by": data.driver_id})
        return {"status": "success", "message": f"Accepted by {data.driver_id}"}
    finally:
        if await redis_client.get(lock_key) == data.driver_id:
            await redis_client.delete(lock_key)


@app.post("/cancel_ride/")
async def cancel_ride(data: dict):
    ride_id = data.get("ride_id")
    r = rides.get(ride_id)
    if not r or r["status"] in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    penalty = 50 if r["status"] == "accepted" else 0
    r["status"] = "cancelled"
    r["penalty"] = penalty
    return {"status": "cancelled", "penalty": penalty}


@sio.event
async def update_driver_location(sid, data):
    ride_id = data.get('ride_id')
    location = data.get('location')
    if not ride_id or ride_id not in rides or not location:
        return

    await sio.emit('rider_route_update', {'ride_id': ride_id, 'location': location})
    route = rides[ride_id].get("route_coordinates", [])
    if route and calculate_haversine_distance(location['lat'], location['lon'], route[-1]['lat'], route[-1]['lon']) > 0.5:
        await sio.emit("route_deviation_alert", {"ride_id": ride_id})


@app.post("/complete_ride/")
async def complete_ride(data: dict):
    ride_id = data.get("ride_id")
    if not ride_id or ride_id not in rides:
        raise HTTPException(status_code=404, detail="Ride not found")
    
    fare = rides[ride_id].get("total_fare", 100.0)
    ledger = calculate_split_ledger(fare)
    rides[ride_id].update({"status": "completed", "ledger": ledger})
    return {"status": "completed", "ride_id": ride_id, "ledger": ledger}


@app.post("/update_location/")
async def update_location(data: dict):
    driver_id = data.get("driver_id")
    if not driver_id or "lat" not in data or "lon" not in data:
        raise HTTPException(status_code=400, detail="Missing driver_id, lat, or lon")
    drivers[driver_id] = {"lat": float(data["lat"]), "lon": float(data["lon"])}
    return {"status": "success"}


@app.post("/get_ride_status/")
async def get_ride_status(d: dict):
    ride_id = d.get("ride_id")
    r = rides.get(ride_id)
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ride_id": ride_id, "status": r.get("status")}


@app.post("/get_ride_history/")
async def get_ride_history(d: dict):
    u = d.get("user_id")
    h = [r for r in rides.values() if r.get("user_id") == u]
    return {"ride_history": h}


@app.post("/select_vehicle/")
async def select_vehicle(d: dict):
    r = rides.get(d.get("ride_id"))
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    r["vehicle_type"] = d.get("vehicle_type")
    return {"status": "success"}


@app.post("/calculate_fare/")
async def calculate_fare(d: dict):
    dist = float(d.get("distance", 1.0))
    v_type = d.get("vehicle_type", "BIKE").upper()
    surge = float(d.get("surge_factor", 1.0))
    base_rates = {"AUTO": 10.0, "CAR": 10.0, "SEDAN": 10.0, "SUV": 10.0, "BIKE": 10.0}
    base = base_rates.get(v_type, 10.0) * max(dist, 1.0)
    fare = calculate_dynamic_fare(base, v_type, surge)
    return {"estimated_fare": round(fare, 2)}


def calculate_dynamic_fare(base_fare: float, category: str, surge_factor: float) -> float:
    category_multipliers = {
        'BIKE': 1.0,
        'AUTO': 1.2,
        'CAR': 1.5,
        'SEDAN': 1.5,
        'SUV': 2.0
    }
    multiplier = category_multipliers.get(category.upper(), 1.0)
    return base_fare * multiplier * surge_factor


@app.post("/reject_ride/")
async def reject_ride(data: dict, background_tasks: BackgroundTasks):
    ride_id = data.get("ride_id")
    rejected_driver = data.get("driver_id")
    r = rides.get(ride_id)
    if not r:
        raise HTTPException(status_code=404, detail="Ride not found")

    r.setdefault("rejected_drivers", []).append(rejected_driver)
    if rejected_driver in r.get("matched_drivers", []):
        r["matched_drivers"].remove(rejected_driver)

    available_drivers = [
        d_id for d_id, loc in drivers.items()
        if calculate_haversine_distance(r["user_lat"], r["user_lon"], loc["lat"], loc["lon"]) <= 0.5
        and d_id not in r["rejected_drivers"]
    ]
    if not available_drivers:
        available_drivers = [
            d_id for d_id, loc in drivers.items()
            if calculate_haversine_distance(r["user_lat"], r["user_lon"], loc["lat"], loc["lon"]) <= 2.0
            and d_id not in r["rejected_drivers"]
        ]

    if not available_drivers:
        r["status"] = "no_drivers"
        return {"status": "failed", "message": "No more drivers available"}

    r["status"] = "searching"
    r["matched_drivers"] = available_drivers
    background_tasks.add_task(send_ride_notifications, ride_id)
    return {"status": "searching_driver", "new_matches": available_drivers}


@app.post("/rate_ride/")
async def rate_ride(data: dict):
    ride_id = data.get("ride_id")
    if ride_id not in rides:
        raise HTTPException(status_code=404, detail="Ride not found")
    rides[ride_id]["rating"] = data.get("rating")
    rides[ride_id]["review"] = data.get("review", "")
    return {"status": "success", "message": "Rating recorded"}


@app.post("/select_route/")
async def select_route(data: dict):
    ride_id = data.get("ride_id")
    if ride_id not in rides:
        raise HTTPException(status_code=404, detail="Ride not found")
    selected_route = data.get("route_name")
    rides[ride_id]["route"] = selected_route
    return {"status": "success", "ride_id": ride_id, "route": selected_route}


@app.post("/api/v1/payments/process")
async def process_payment(data: dict):
    ride_id = data.get("ride_id")
    amount = data.get("amount")
    if ride_id not in rides:
        raise HTTPException(status_code=404, detail="Ride not found")
    return {"status": "success", "ride_id": ride_id, "amount": amount}


async def confirm_ride_closure(ride_id: str, role: str):
    if ride_id in rides:
        rides[ride_id][f"{role}_confirmed"] = True
        if rides[ride_id].get("driver_confirmed") and rides[ride_id].get("rider_confirmed"):
            rides[ride_id]["status"] = "closed"


@app.post("/confirm_closure/")
async def handle_ride_closure(data: dict):
    ride_id = data.get("ride_id")
    role = data.get("role")
    if not ride_id or role not in ["driver", "rider"]:
        raise HTTPException(status_code=400, detail="Invalid parameters")
    await confirm_ride_closure(ride_id, role)
    return {"status": "success", "message": f"{role} confirmation recorded for ride {ride_id}"}
 


def calculate_split_ledger(total_fare: float) -> dict:
    return {
        "platform": round(total_fare * 0.20, 2),
        "driver": round(total_fare * 0.80, 2)
    }


async def notify_emergency_contacts(ride_id: str):
    await sio.emit("sos_alert", {"ride_id": ride_id, "level": "emergency_contacts"})


async def dispatch_108_emergency(ride_id: str):
    await sio.emit("sos_alert", {"ride_id": ride_id, "level": "dispatch_108"})


async def trigger_sos_escalation(ride_id: str, stage: int):
    if stage == 1:
        await notify_emergency_contacts(ride_id)
    elif stage == 2:
        await dispatch_108_emergency(ride_id)

@app.post("/trigger_sos/")
async def handle_sos_escalation(data: dict):
    ride_id = data.get("ride_id")
    stage = data.get("stage")
    if not ride_id or stage not in [1, 2]:
        raise HTTPException(status_code=400, detail="Invalid request parameters")
    await trigger_sos_escalation(ride_id, stage)
    return {"status": "success", "message": f"SOS stage {stage} triggered for ride {ride_id}"}




async def initiate_masked_call(driver_phone: str, rider_phone: str):
    url = 'https://api.masking.service/v1/call'
    payload = {'driver_phone': driver_phone, 'rider_phone': rider_phone}
    headers = {'Authorization': 'Bearer YOUR_API_KEY'}
    async with httpx.AsyncClient() as client: response = await client.post(url, json=payload, headers=headers, timeout=5) 
    return response.json()


@app.post("/masked_call/")
async def make_masked_call(data: dict):
    driver_phone = data.get("driver_phone")
    rider_phone = data.get("rider_phone")
    if not driver_phone or not rider_phone:
        raise HTTPException(status_code=400, detail="Missing phone numbers")
    response = await initiate_masked_call(driver_phone, rider_phone)
    return {"status": "success", "response": response}



def generate_referral_code(user_id: str) -> str:
    return f'REF{uuid.uuid4().hex[:6].upper()}'


@app.post('/apply_referral/')
async def apply_referral(data: dict):
    referrer_id = data.get('referrer_id')
    referee_id = data.get('referee_id')
    return {'status': 'success', 'message': 'Referral applied', 'referrer_id': referrer_id, 'referee_id': referee_id}

civic_reports = {}


class CivicIssueReport(BaseModel):
    reporter_id: str
    role: str  # "driver" or "rider"
    issue_type: str = "pothole"
    image_url: str
    lat: float
    lon: float
    ward_or_area: str
    municipal_body: str = "GVMC"
    mla_handle: str = "@LocalMLA"
    social_handle: str = ""
    description: str = ""


@app.post("/report_civic_issue/")
async def report_civic_issue(report: CivicIssueReport):
    report_id = f"CIVIC-{uuid.uuid4().hex[:8].upper()}"
    report_data = report.model_dump() if hasattr(report, "model_dump") else report.dict()

    social_post_text = (
        f"🚨 Civic Issue Alert ({report.issue_type.upper()}) at {report.ward_or_area} "
        f"(GeoTag: {report.lat}, {report.lon}). "
        f"Requesting immediate attention from @{report.municipal_body} {report.mla_handle} "
        f"Reported via #Rathsarthi by {report.social_handle or report.reporter_id}"
    )

    civic_reports[report_id] = {
        **report_data,
        "report_id": report_id,
        "status": "reported_to_authorities",
        "social_caption": social_post_text
    }

    await sio.emit("civic_hazard_alert", {
        "report_id": report_id,
        "issue_type": report.issue_type,
        "lat": report.lat,
        "lon": report.lon,
        "ward_or_area": report.ward_or_area
    })

    return {
        "status": "success",
        "report_id": report_id,
        "notified_authorities": [report.municipal_body, report.mla_handle,],
        "social_media_payload": {
            "linked_handle": report.social_handle,
            "image_url": report.image_url,
            "geotag": {"lat": report.lat, "lon": report.lon},
            "caption": social_post_text
        }
    }


@app.get("/get_civic_reports/")
async def get_civic_reports():
    return {"civic_reports": list(civic_reports.values())}



if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
