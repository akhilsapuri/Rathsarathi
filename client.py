import socketio

sio = socketio.Client()


@sio.event
def connect():
    print("Connected to server")


@sio.on("ride_requested")
def on_ride_requested(data):
    print("Notification received:", data)


sio.connect("http://localhost:8000")
sio.wait()
