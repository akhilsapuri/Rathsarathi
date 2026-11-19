import firebase_admin
from firebase_admin import credentials, auth
from flask import Flask, request, jsonify

app = Flask(__name__)

cred = credentials.Certificate(r'C:\Users\srava\frontend\serviceAccountKey.json')
firebase_admin.initialize_app(cred)

@app.route('/send-otp', methods=['POST'])
def send_otp():

    data = request.get_json()
    phone_number = data.get('phone_number')
    
    if not phone_number:
        return jsonify({"message": "Phone number required"}), 400
        
    return jsonify({
        "status": "success", 
        "message": "Use Firebase Client SDK on frontend to send OTP."
    }), 200

if __name__ == '__main__':
    app.run(debug=True)
