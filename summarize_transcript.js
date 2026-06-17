const fs = require('fs');
const { GoogleGenerativeAI } = require('@google/generative-ai');

// Extract API key directly from .env file
const envContent = fs.readFileSync('/Users/tray/Desktop/Trading/.env', 'utf8');
let apiKey = '';
envContent.split('\n').forEach(line => {
  if (line.startsWith('GEMINI_API_KEY=')) {
    apiKey = line.split('=')[1].trim().replace(/['"]/g, '');
  }
});

const genAI = new GoogleGenerativeAI(apiKey);

const transcript = fs.readFileSync('/Users/tray/Downloads/IMG_2759.txt', 'utf8');

const prompt = `
You are an expert transcriber and analyst. Below is a raw, un-speaker-diarized transcript from an audio recording.
The conversation involves three people: "Tray" (me, the person who recorded it), "Joey", and "Anthony".
Based on context clues (e.g., who asks questions, who demonstrates a platform, who explains the business model, references to Drata, sales processes, or specific names), please reconstruct the dialogue.

Please produce a formatted document where each line of dialogue is attributed to the correct speaker (Tray, Joey, or Anthony). 
If it is impossible to distinguish between Joey and Anthony for a specific line, you can label it "Joey/Anthony".
Focus on Tray as the one asking questions about the platform, Drata, and sales strategy.
The other two (Joey and Anthony) are the ones explaining the VigilantSec platform, their business model, and the free assessment tool.

Transcript:
${transcript}
`;

async function run() {
  try {
    const model = genAI.getGenerativeModel({ model: 'gemini-1.5-pro' });
    const result = await model.generateContent(prompt);
    const response = await result.response;
    const text = response.text();
    
    fs.writeFileSync('/Users/tray/Documents/Dolph & Tray/Drata Hub/VigilantSec_Meeting_Transcript.md', text);
    console.log("Saved to /Users/tray/Documents/Dolph & Tray/Drata Hub/VigilantSec_Meeting_Transcript.md");
  } catch (e) {
    console.error("Error:", e.message);
  }
}

run();
