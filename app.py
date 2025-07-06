import streamlit as st
import requests
import pandas as pd
import datetime as dt
from datetime import datetime, timedelta, timezone
from geopy.distance import great_circle
from openai import OpenAI
import re
import pytz
from pytz import timezone as tz

# Initialize clients
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY", ""))

# ========== UTILITY FUNCTIONS ==========
def uv_risk_level(uv_index):
    """Determine UV risk level"""
    if uv_index <= 2: return "Low"
    elif uv_index <= 5: return "Moderate"
    elif uv_index <= 7: return "High"
    elif uv_index <= 10: return "Very High"
    else: return "Extreme"

def translate_weather_code(code):
    """Convert Open-Meteo weather codes to text descriptions"""
    weather_codes = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy",
        3: "Overcast", 45: "Fog", 51: "Light drizzle",
        61: "Light rain", 80: "Rain showers", 95: "Thunderstorm"
    }
    return weather_codes.get(code, "Unknown weather conditions")

def get_safety_rating(weather):
    """Generate safety rating (0-10) based on weather conditions"""
    # Base rating
    rating = 8.0
    
    # Adjust based on conditions
    if "Thunderstorm" in weather['conditions']:
        rating -= 4
    elif "Rain" in weather['conditions']:
        rating -= 2
    elif "Fog" in weather['conditions']:
        rating -= 1
        
    # Adjust based on temperature
    if weather['temp'] > 35:
        rating -= 2
    elif weather['temp'] < 10:
        rating -= 1
        
    # Adjust based on wind
    if weather['wind'] > 30:
        rating -= 3
    elif weather['wind'] > 20:
        rating -= 1
        
    # Adjust based on UV
    if weather['uv_index'] > 8:
        rating -= 1
        
    # Ensure rating is within bounds
    return max(0, min(10, round(rating, 1)))

def estimate_flight_distance(origin, destination):
    """Calculate air distance using coordinates"""
    orig_lat, orig_lng, _ = get_coordinates(origin)
    dest_lat, dest_lng, _ = get_coordinates(destination)
    if None in [orig_lat, orig_lng, dest_lat, dest_lng]:
        return 0
    return great_circle((orig_lat, orig_lng), (dest_lat, dest_lng)).km

def generate_packing_list(weather, safety_score, location):
    """AI-generated packing recommendations with location context"""
    if client and st.secrets.get("OPENAI_API_KEY"):
        try:
            prompt = f"""Generate a concise packing list for traveling to {location} with:
            - Weather: {weather['conditions']} ({weather['temp']}°C)
            - Humidity: {weather['humidity']}%
            - Wind: {weather['wind']} km/h
            - Safety rating: {safety_score}/10
            Format as bullet points with emojis. Max 8 items. Be specific to the location."""
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200
            )
            return response.choices[0].message.content
        except:
            return "❌ Couldn't generate AI packing list"
    return "🔑 Enable OpenAI API for smart packing suggestions"

def generate_beach_packing_list(weather, uv_index):
    """Generate beach-specific packing list"""
    items = ["🧴 SPF 50+ Sunscreen", "🩱 Swimwear", "🏖️ Beach towel", 
             "🕶️ UV-protection sunglasses", "💧 Reusable water bottle"]
    
    if uv_index > 8:
        items.append("🧢 UV-protective hat/clothing")
    if weather['temp'] > 30:
        items.append("🌬️ Portable fan/misting bottle")
    if weather['wind'] > 15:
        items.append("🧥 Light windbreaker")
    if "rain" in weather['conditions'].lower():
        items.append("☔ Waterproof bag/cover")
    
    return items

def get_beach_safety_score(uv_index, hazards, has_lifeguard):
    """Calculate beach-specific safety score"""
    score = 8  # Base score
    
    # UV impact
    if uv_index > 8: score -= 2
    elif uv_index > 5: score -= 1
    
    # Hazard impact
    if any("tsunami" in h.lower() for h in hazards): score -= 3
    elif any("cyclone" in h.lower() for h in hazards): score -= 2
    
    # Lifeguard bonus
    if has_lifeguard: score += 1
    
    return max(1, min(10, score))

def generate_location_guide(location, weather):
    """Generate ChatGPT-like output about the location"""
    if not client or not st.secrets.get("OPENAI_API_KEY"):
        return "🔑 Enable OpenAI API for location guide"
    
    try:
        prompt = f"""
        Create a comprehensive travel guide for {location} in exactly 3 paragraphs (about 700 words total). 
        Include the following sections:
        
        Paragraph 1 (About the Place):
        - Brief introduction to the location
        - How to reach
        - Historical significance
        - Cultural highlights
        - Current weather: {weather['conditions']} at {weather['temp']}°C
        
        Paragraph 2 (Things to Do and See):
        - Top attractions and landmarks
        - Popular activities
        - Famous sites
        
        Paragraph 3 (Food and Culture):
        - Local cuisine and must-try dishes
        - Cultural experiences
        - Shopping recommendations
        - Travel tips
        
        Write in an engaging, informative style similar to a travel blogger.
        """
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            stream=True
        )
        
        return response
        
    except Exception as e:
        return f"❌ Error generating guide: {str(e)}"

# ========== API FUNCTIONS WITH CACHING ==========
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_coordinates(location):
    """Geocode using Google Maps"""
    try:
        if 'GOOGLE_MAPS_API_KEY' not in st.secrets:
            return None, None, None
            
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": f"{location}, India",
            "key": st.secrets["GOOGLE_MAPS_API_KEY"]
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data.get('results'):
            loc = data['results'][0]['geometry']['location']
            return loc['lat'], loc['lng'], data['results'][0]['formatted_address']
    except Exception as e:
        st.error(f"Geocoding error: {str(e)}")
    return None, None, None

@st.cache_data(ttl=1800)  # Cache for 30 minutes
def get_weather(lat, lng):
    """Enhanced weather data with UV index"""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lng,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code,uv_index",
            "hourly": "temperature_2m",
            "daily": "uv_index_max",
            "timezone": "auto"
        }
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "temp": data['current']['temperature_2m'],
                "humidity": data['current']['relative_humidity_2m'],
                "wind": data['current']['wind_speed_10m'],
                "uv_index": data['current']['uv_index'],
                "uv_max": data['daily']['uv_index_max'][0],
                "conditions": translate_weather_code(data['current']['weather_code']),
                "forecast": {
                    "max": max(data['hourly']['temperature_2m'][:24]),
                    "min": min(data['hourly']['temperature_2m'][:24])
                }
            }
    except:
        pass
    
    # Fallback
    return {
        "temp": 28.0,
        "humidity": 65,
        "wind": 12.0,
        "uv_index": 6.5,
        "uv_max": 8.2,
        "conditions": "Sunny",
        "forecast": {"max": 32, "min": 26}
    }

@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_tide_data(lat, lng):
    """Fetch tide extremes using Storm Glass API"""
    try:
        if "STORMGLASS_API_KEY" not in st.secrets:
            return {"error": "No Storm Glass API key configured"}

        # Get today and tomorrow dates in UTC
        today = dt.datetime.utcnow().date().isoformat()
        tomorrow = (dt.datetime.utcnow().date() + dt.timedelta(days=1)).isoformat()

        url = "https://api.stormglass.io/v2/tide/extremes/point"
        params = {"lat": lat, "lng": lng, "start": today, "end": tomorrow}
        headers = {"Authorization": st.secrets["STORMGLASS_API_KEY"]}

        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "data" not in data or not data["data"]:
            return {"error": "No tide data available"}

        # Get current time as AWARE datetime
        now_utc = datetime.now(timezone.utc)
        next_high = None
        next_low = None

        for tide in data["data"]:
            # Parse as aware datetime
            tide_time = datetime.fromisoformat(tide["time"].replace('Z', '+00:00'))
            
            # Skip past tides
            if tide_time < now_utc:
                continue

            # Process high tide
            if tide["type"] == "high":
                if not next_high or tide_time < next_high["time"]:
                    next_high = {
                        "time": tide_time,
                        "height": tide.get("height", "N/A")
                    }
            
            # Process low tide
            elif tide["type"] == "low":
                if not next_low or tide_time < next_low["time"]:
                    next_low = {
                        "time": tide_time,
                        "height": tide.get("height", "N/A")
                    }

        return {"next_high": next_high, "next_low": next_low}

    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=86400)  # Cache for 24 hours
def get_water_quality(location):
    """Get water quality rating (simulated)"""
    try:
        # In a real implementation, use Google Places API
        qualities = ["Excellent", "Good", "Fair", "Poor"]
        return qualities[(len(location) + datetime.now().hour) % 4]
    except:
        return "Unknown"

@st.cache_data(ttl=86400)  # Cache for 24 hours
def get_beach_facilities(lat, lng):
    """Detect nearby beach facilities (simulated)"""
    try:
        facilities = []
        if "beach" in st.session_state.get('location', "").lower():
            facilities = ["Lifeguard", "First Aid", "Showers", "Restrooms"]
        
        # Add dynamic facilities based on location
        if "goa" in st.session_state.get('location', "").lower():
            facilities += ["Water Sports", "Beach Shacks"]
        elif "puri" in st.session_state.get('location', "").lower():
            facilities += ["Changing Rooms", "Beach Chairs"]
            
        return facilities[:4]  # Return max 4 facilities
    except:
        return []

# ========== UI COMPONENTS ==========
def display_beach_report(location, lat, lng):
    """Show beach-specific safety information"""
    with st.spinner(f"Analyzing beach conditions at {location}..."):
        weather = get_weather(lat, lng)
        tide_data = get_tide_data(lat, lng)
        facilities = get_beach_facilities(lat, lng)
        
        # Safety rating based on weather
        safety_rating = get_safety_rating(weather)
        
        st.subheader("🏖️ Beach Safety Report")
        
        # Safety Rating
        st.subheader("⚠️ Safety to Visit?")
        st.metric("Safety Rating", f"{safety_rating}/10", 
                 "Very Safe" if safety_rating >= 8 else 
                 "Safe" if safety_rating >= 6 else 
                 "Caution Advised" if safety_rating >= 4 else 
                 "Not Recommended")
        
        # Weather Conditions
        st.subheader("🌤️ Current Weather")
        col1, col2 = st.columns(2)
        col1.metric("Temperature", f"{weather['temp']}°C")
        col1.metric("Conditions", weather['conditions'])
        col2.metric("Wind Speed", f"{weather['wind']} km/h")
        col2.metric("Humidity", f"{weather['humidity']}%")
        
        # Tide Information
        st.subheader("🌊 Tide Information")
        if tide_data and "error" not in tide_data:
            # Convert UTC to local time
            ist = tz('Asia/Kolkata')
            
            col1, col2 = st.columns(2)
            if tide_data.get("next_high"):
                high_time = tide_data["next_high"]["time"].astimezone(ist)
                col1.metric("Next High Tide", 
                          high_time.strftime("%H:%M"), 
                          f"{tide_data['next_high']['height']}m")
            
            if tide_data.get("next_low"):
                low_time = tide_data["next_low"]["time"].astimezone(ist)
                col2.metric("Next Low Tide", 
                          low_time.strftime("%H:%M"), 
                          f"{tide_data['next_low']['height']}m")
        else:
            error_msg = tide_data.get("error", "Tide data unavailable")
            st.warning(f"{error_msg}")
            
        # Water Quality
        st.subheader("💧 Water Quality")
        water_quality = get_water_quality(location)
        st.metric("Swimming Safety", water_quality, 
                 "Safe for swimming" if water_quality in ["Good", "Excellent"] else "Check conditions")
        
        # Packing List
        with st.expander("🧳 Beach Packing Essentials"):
            st.write("**Recommended items for your beach trip:**")
            for item in generate_beach_packing_list(weather, weather['uv_index']):
                st.write(f"- {item}")

def display_location_report(location):
    """Show complete safety and weather report"""
    if not location:
        st.warning("Please enter a location")
        return
        
    lat, lng, name = get_coordinates(location)
    if not lat or not lng:
        st.error(f"Could not find coordinates for: {location}")
        return
    
    st.session_state['location'] = location.lower()
    
    with st.spinner(f"Analyzing {name or location}..."):
        weather = get_weather(lat, lng)
        
        # Safety rating based on weather
        safety_rating = get_safety_rating(weather)
        
        # Weather Section
        st.subheader("🌤️ Current Weather")
        col1, col2 = st.columns(2)
        col1.metric("Temperature", f"{weather['temp']}°C")
        col1.metric("Conditions", weather['conditions'])
        col2.metric("Wind Speed", f"{weather['wind']} km/h")
        col2.metric("Humidity", f"{weather['humidity']}%")
        
        st.subheader("📅 Today's Forecast")
        st.write(f"High: {weather['forecast']['max']}°C | Low: {weather['forecast']['min']}°C")
        
        # Safety Section
        st.subheader("⚠️ Is It Safe to Visit?")
        st.metric("Safety Rating", f"{safety_rating}/10", 
                 "Very Safe" if safety_rating >= 8 else 
                 "Safe" if safety_rating >= 6 else 
                 "Caution Advised" if safety_rating >= 4 else 
                 "Not Recommended")
        
        # Packing List
        with st.expander("🧳 What to Carry"):
            st.write(generate_packing_list(weather, safety_rating, location))
        
        # Check if location is a beach
        if "beach" in location.lower() or any(x in location.lower() for x in ["coast", "shore", "seaside"]):
            display_beach_report(location, lat, lng)
        
        # Location Guide
        st.subheader("📚 Travel Guide")
        guide = generate_location_guide(location, weather)
        
        if isinstance(guide, str):
            st.write(guide)
        else:
            response_container = st.empty()
            full_response = ""
            
            for chunk in guide:
                if chunk.choices[0].delta.content is not None:
                    word = chunk.choices[0].delta.content
                    full_response += word
                    response_container.markdown(full_response + "▌")
            
            response_container.markdown(full_response)

# ========== MAIN APP ==========
st.set_page_config(
    page_title="🏖️ Beach Safety Analyzer",
    page_icon="🌊",
    layout="wide"
)

st.title("🏖️ Beach Safety Analyzer")
st.markdown("""
    **Stay safe at the beach!** Get real-time safety analysis, weather conditions, 
    tide information, and essential packing recommendations for beach destinations.
""")

location = st.text_input("Enter beach name or location:", 
                        placeholder="Puri Beach, Odisha",
                        key="beach_input")

if st.button("Analyze Location") or location:
    if location:
        display_location_report(location)
    else:
        st.warning("Please enter a beach location")