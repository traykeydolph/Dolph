import os
import requests
import json

# Extract API key directly from .env file
api_key = ''
with open('/Users/tray/Desktop/Trading/.env', 'r') as f:
    for line in f:
        if line.startswith('GEMINI_API_KEY='):
            api_key = line.split('=', 1)[1].strip().strip("'\"")
            break

with open('/Users/tray/Downloads/IMG_2759.txt', 'r') as f:
    transcript = f.read()

prompt = f"""
You are an expert transcriber and analyst. Below is a raw, un-speaker-diarized transcript from an audio recording.
The conversation involves three people: "Tray" (me, the person who recorded it), "Joey", and "Anthony".
Based on context clues (e.g., who asks questions, who demonstrates a platform, who explains the business model, references to Drata, sales processes, or specific names), please reconstruct the dialogue.

Please produce a formatted document where each line of dialogue is attributed to the correct speaker (Tray, Joey, or Anthony). 
If it is impossible to distinguish between Joey and Anthony for a specific line, you can label it "Joey/Anthony".
Focus on Tray as the one asking questions about the platform, Drata, and sales strategy.
The other two (Joey and Anthony) are the ones explaining the VigilantSec platform, their business model, and the free assessment tool.

Transcript:
{transcript}
"""

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={api_key}"
headers = {'Content-Type': 'application/json'}
data = {
    "contents": [{"parts": [{"text": prompt}]}]
}

response = requests.post(url, headers=headers, json=data)

if response.status_code == 200:
    result = response.json()
    text = result['candidates'][0]['content']['parts'][0]['text']
    with open('/Users/tray/Documents/Dolph & Tray/Drata Hub/VigilantSec_Meeting_Transcript.md', 'w') as f:
        f.write(text)
    print("Saved to /Users/tray/Documents/Dolph & Tray/Drata Hub/VigilantSec_Meeting_Transcript.md")
else:
    print(f"Error: {response.status_code}")
    print(response.text)
