import streamlit as st
from PIL import Image
import base64
from io import BytesIO
import time
import requests  # Import the requests library

# Set page config
st.set_page_config(page_title="Chitragupta – College Virtual Assistance Chatbot", layout="wide")

# Encode KMIT logo to base64
kmit_logo = Image.open("download.jpeg")
buffered = BytesIO()
kmit_logo.save(buffered, format="JPEG")
img_str = base64.b64encode(buffered.getvalue()).decode()

# Custom CSS
st.markdown(""" 
<style>
body {
    background-color: white;
    color: black;
}
.user-message {
    display: inline-block;
    max-width: 70%;
    text-align: right;
    background-color: #E6E6E6;
    border-radius: 10px;
    padding: 10px;
    margin: 5px 0;
    float: right;
    clear: both;
}
.assistant-message {
    display: inline-block;
    max-width: 70%;
    text-align: left;
    background-color: #E6E6E6;
    border-radius: 10px;
    padding: 10px;
    margin: 5px 0;
    float: left;
    clear: both;
}
.streamlit-expanderHeader {
    color: black;
}
.stTextInput, .stTextArea, .stChatInput {
    background-color: #F0F0F0;
    color: black;
    border-radius: 5px;
}
.stButton, .stSlider, .stSelectbox, .stRadio {
    background-color: #F0F0F0;
    color: black;
}
</style>
""", unsafe_allow_html=True)

# Display title
st.markdown(f"""
<h1 style='display: flex; align-items: center; gap: 15px;'>
    <img src="data:image/jpeg;base64,{img_str}" width="50">
    Chitragupta – College Virtual Assistance Chatbot
</h1>
""", unsafe_allow_html=True)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.greeted = False

# Delay + greet only once
if not st.session_state.greeted:
    time.sleep(1)  # Delay of 1 second
    st.session_state.messages.append({
        "role": "assistant",
        "content": "Hi, I am Chitragupta! How may I help you?"
    })
    st.session_state.greeted = True

# Space before chat history
st.markdown("<br><br>", unsafe_allow_html=True)

# Display previous messages
for msg in st.session_state.messages:
    if msg["role"] == "user":
        st.markdown(f'<div class="user-message">{msg["content"]}</div>', unsafe_allow_html=True)
    elif msg["role"] == "assistant":
        st.markdown(f'<div class="assistant-message">{msg["content"]}</div>', unsafe_allow_html=True)

# Chat input
if user_input := st.chat_input("Ask something about admissions, placements, courses..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.markdown(f'<div class="user-message">{user_input}</div>', unsafe_allow_html=True)

    # Show assistant reply
    with st.chat_message("assistant"):
        msg_placeholder = st.empty()
        msg_placeholder.markdown("Thinking...")

        #  Send query to backend and get response
        try:
            response = requests.post("http://localhost:5000/query", json={"query": user_input})
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()
            if "response" in data:
                chatbot_response = data["response"]
                msg_placeholder.markdown(f'<div class="assistant-message">{chatbot_response}</div>', unsafe_allow_html=True)
                st.session_state.messages.append({"role": "assistant", "content": chatbot_response})
            else:
                msg_placeholder.markdown(f'<div class="assistant-message">Sorry, I could not process your request.  The backend did not provide a response.</div>', unsafe_allow_html=True)
                st.session_state.messages.append({"role": "assistant", "content": "Sorry, I could not process your request. The backend did not provide a response."})

        except requests.exceptions.RequestException as e:
            error_message = f"Error: Could not connect to the backend.  Please ensure the server is running and accessible.  Details: {e}"
            msg_placeholder.markdown(f'<div class="assistant-message">{error_message}</div>', unsafe_allow_html=True)
            st.session_state.messages.append({"role": "assistant", "content": error_message})

