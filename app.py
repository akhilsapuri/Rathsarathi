import streamlit as st
import requests
Base_URL = "http://localhost:8000"


def get_root():
    response = requests.get(Base_URL)
    st.write(response.json())

if st.button("Get Data"):
    get_root()

st.title("Pothole Analyzer")

image_path = st.text_input("Enter Image Path:")

if st.button("Analyze Pothole"):
    if image_path:
        response = requests.post(f"{BASE_URL}/analyze", json={"image_path": image_path})
        st.write(response.json())
else:
    st.warning("Please enter an image path.")
