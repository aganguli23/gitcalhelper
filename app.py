import os
import json
import re
import warnings
import io
import contextlib
import datetime
import uuid
import webbrowser
from datetime import datetime, timezone

# Load environment variables from .env file using python-dotenv
from dotenv import load_dotenv
load_dotenv()

# Silence deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# External imports
from openai import OpenAI
from pvrecorder import PvRecorder
from playsound import playsound
from IPython.display import Image, display

# Flask and related modules
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

# Google OAuth and Calendar Imports
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# OCR and file conversion Imports
from PIL import Image as PILImage
import pytesseract
from pdf2image import convert_from_path
from docx2pdf import convert

# Time zone imports
from tzlocal import get_localzone
from zoneinfo import ZoneInfo

# Define the scope for Google Calendar API (read/write access)
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Initialize Flask app and set secret key from environment variables
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_secret_key")
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Allowed file extensions for upload
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ------------------------------
# Google Calendar API Utilities
# ------------------------------
def create_credentials_file():
    """
    Create credentials.json using environment variables.
    If the file already exists, leave it intact.
    """
    if not os.path.exists('credentials.json'):
        # Read sensitive credential values from environment variables
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        google_project_id = os.environ.get("GOOGLE_PROJECT_ID", "your_project_id")

        if not google_client_id or not google_client_secret:
            raise Exception("Google client ID and/or client secret are not set in environment variables!")
        
        credentials_data = {
            "installed": {
                "client_id": google_client_id,
                "project_id": google_project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": google_client_secret,
                "redirect_uris": [
                    "urn:ietf:wg:oauth:2.0:oob",
                    "http://localhost"
                ]
            }
        }
        with open('credentials.json', 'w') as f:
            json.dump(credentials_data, f, indent=4)
        print("credentials.json created successfully!")
    else:
        print("credentials.json already exists.")


def authenticate():
    """
    Authenticate with Google Calendar using OAuth and return credentials.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        try:
            # Launch a local server for OAuth
            creds = flow.run_local_server(port=0)
        except Exception:
            print("Error launching local server for authentication, falling back to console input.")
            creds = flow.run_console()
        with open('token.json', 'w') as token_file:
            token_file.write(creds.to_json())
        print("token.json created successfully!")
    return creds


# ------------------------------
# OCR Functionality with Page Selection
# ------------------------------
def ocr_image(file_path_or_object, selected_pages=None):
    """
    Perform OCR on a file (image, PDF, or DOCX).
    If selected_pages is provided, only process those pages (1-indexed).
    """
    try:
        if isinstance(file_path_or_object, str):
            if file_path_or_object.lower().endswith(".pdf"):
                pdf_images = convert_from_path(file_path_or_object, dpi=300)
                if selected_pages:
                    filtered_images = []
                    for page_num in selected_pages:
                        if 1 <= page_num <= len(pdf_images):
                            filtered_images.append(pdf_images[page_num - 1])
                    pdf_images = filtered_images

                extracted_text = ""
                for page_number, image in enumerate(pdf_images, start=1):
                    print(f"Processing page {page_number} from PDF...")
                    page_text = pytesseract.image_to_string(image)
                    extracted_text += f"--- Page {page_number} ---\n{page_text}\n"
                print("OCR extracted text from PDF:", extracted_text)
                return extracted_text

            elif file_path_or_object.lower().endswith(".docx"):
                temp_pdf = file_path_or_object.rsplit('.', 1)[0] + '_temp.pdf'
                print(f"Converting DOCX to PDF: {file_path_or_object} -> {temp_pdf}")
                try:
                    convert(file_path_or_object, temp_pdf)
                    print("DOCX conversion successful.")
                except Exception as e:
                    print(f"Error during DOCX to PDF conversion: {e}")
                    return ""
                pdf_images = convert_from_path(temp_pdf, dpi=300)
                if selected_pages:
                    filtered_images = []
                    for page_num in selected_pages:
                        if 1 <= page_num <= len(pdf_images):
                            filtered_images.append(pdf_images[page_num - 1])
                    pdf_images = filtered_images

                extracted_text = ""
                for page_number, image in enumerate(pdf_images, start=1):
                    print(f"Processing page {page_number} from DOCX (converted PDF)...")
                    page_text = pytesseract.image_to_string(image)
                    extracted_text += f"--- Page {page_number} ---\n{page_text}\n"
                os.remove(temp_pdf)
                print("OCR extracted text from DOCX:", extracted_text)
                return extracted_text

            else:
                # Assume the file is an image
                image = PILImage.open(file_path_or_object)
                text = pytesseract.image_to_string(image)
                print("OCR extracted text from image:", text)
                return text

        else:
            # Process a file-like object as an image
            image = PILImage.open(file_path_or_object)
            text = pytesseract.image_to_string(image)
            print("OCR extracted text from image:", text)
            return text

    except Exception as e:
        print("Error during OCR:", e)
        return ""


# ------------------------------
# Input Combination Function
# ------------------------------
def combine_inputs(user_input, ocr_text):
    """
    Combine the single text input with OCR extracted text.
    """
    combined_parts = []
    if user_input:
        combined_parts.append(user_input)
    if ocr_text:
        combined_parts.append(ocr_text)
    return " ".join(combined_parts)


# ------------------------------
# GPT-4o Chatbot Class
# ------------------------------
class GPT4o:
    def __init__(self, client, json_file='gpt4oContext1.json'):
        self.client = client
        self.context = []
        self.json_file = json_file

    def chat(self, message, save=False):
        message = (message or "") + " "

        if not self.context:
            self.context.append({"role": "system", "content": "You are a helpful assistant."})

        self.context.append({"role": "user", "content": message})

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=self.context
        )
        response_content = response.choices[0].message.content

        self.context.append({"role": "assistant", "content": response_content})
        self.save_to_json(message, response_content, save)

        if not save:
            json_files_to_clear = ['gpt4oContext1.json', 'gpt4oMiniContext1.json', 'gpt3pt5TurboContext1.json']
            self.clear_json_files(json_files_to_clear)

        self.print_response(response_content)
        return response_content

    def clear_json_files(self, json_files):
        for json_file in json_files:
            with open(json_file, 'w') as file:
                json.dump({}, file)
            print(f"The contents of {json_file} have been cleared.")

    def save_to_json(self, input_text, output_text, save):
        if save:
            try:
                with open(self.json_file, 'r') as file:
                    data = json.load(file)
            except FileNotFoundError:
                data = {}
            data[input_text] = output_text
            with open(self.json_file, 'w') as file:
                json.dump(data, file, indent=4)
            print(f"Data successfully saved to {self.json_file}.")
        else:
            print("Save flag is False. No data was saved.")

    def print_response(self, response_content):
        print(f'BOT: {response_content}')

    def print_full_chat(self):
        for message in self.context:
            if message["role"] == "user":
                print(f'USER: {message["content"]}')
            elif message["role"] == "assistant":
                print(f'BOT: {message["content"]}')
        if self.context:
            print("\nFINAL OUTPUT")
            print(f'BOT: {self.context[-1]["content"]}')


# Initialize OpenAI client with API key from the environment
openai_api_key = os.environ.get("OPENAI_API_KEY")
if not openai_api_key:
    raise Exception("OPENAI_API_KEY not set in environment variables!")
client = OpenAI(api_key=openai_api_key)
gpt4o = GPT4o(client)

def get_gpt4o_response(input_text):
    try:
        print("Sending to GPT-4o:", input_text)
        response_content = gpt4o.chat(f"{input_text}")
        if not response_content:
            print("GPT-4o returned an empty response.")
            return None
        return response_content.strip()
    except Exception as e:
        print(f"Error during GPT-4o call: {e}")
        return None

def extract_code(response_text):
    """
    Extract Python code from a GPT output formatted in a code block.
    """
    code_match = re.search(r"```python\s*(.*?)\s*```", response_text, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()
    return ""

# ------------------------------
# Flask Routes
# ------------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/process", methods=["POST"])
def process():
    # Get the single consolidated input
    user_input = request.form.get("text_input", "")
    ocr_text = ""
    selected_pages = []
    selected_pages_str = request.form.get("selected_pages", "")
    if selected_pages_str:
        try:
            selected_pages = [int(num.strip()) for num in selected_pages_str.split(",") if num.strip().isdigit()]
            if len(selected_pages) > 2:
                flash("Please select a maximum of 2 pages.")
                return redirect(url_for('index'))
        except ValueError:
            flash("Invalid page numbers entered.")
            return redirect(url_for('index'))

    file = request.files.get("file_upload")
    if file and file.filename != "":
        if not allowed_file(file.filename):
            flash("File type not allowed! Please upload an image, PDF, or DOCX file.")
            return redirect(url_for('index'))
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        ext = filename.rsplit('.', 1)[1].lower()
        if ext in ['pdf', 'docx']:
            ocr_text = ocr_image(file_path, selected_pages=selected_pages)
        else:
            with open(file_path, "rb") as f:
                ocr_text = ocr_image(f)
        os.remove(file_path)

    combined_input = combine_inputs(user_input, ocr_text)
    print("Combined Input:", combined_input)

    prompt = f"""
Generate a Python script to add all the calendar event(s) (that you identify in this user input text)
to Google Calendar: {combined_input}.
Carefully meet all the criteria and follow all the directions below:
All API setup has been completed and authentication is managed via OAuth2 using the "installed" client credentials defined in credentials.json.
Ensure that the script utilizes InstalledAppFlow (from google_auth_oauthlib.flow) for user authentication and stores tokens in token.json.
Do not use service account credentials, as those require fields (such as client_email and token_uri) which are not present in credentials.json.
Use the Google Calendar API and include proper timezone handling by
    1. Setting the Time in Local Timezone: The start_time is now set in the local timezone (local_tz) instead of UTC: start_time = datetime.combine(next_wednesday, datetime.min.time(), tzinfo=local_tz)
    2. Avoiding Unnecessary UTC Conversion: By setting the time directly in the local timezone, you avoid the need to convert from UTC to local time later.
    3. Ensuring Correct Time in Google Calendar: The create_calendar_event function already sends the local time and timezone to Google Calendar:
    'dateTime': start_local.isoformat(),
    'timeZone': str(local_tz)
    This ensures that the event is created at the correct time in your local timezone.

Ensure the year and month are correct. If not provided, extract from:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
and convert to local time.
Ensure that event titles are human yet professional--short, concise, and descriptive.
Unless otherwise specified, include reminders at 10 minutes, 1 hour, and 1 day before as notifications.
If a Google Meet link is explicitly required and certain, include conferenceData with a createRequest (using a unique requestId and conferenceSolutionKey set as 'hangoutsMeet'), and when calling events.insert or events.update include conferenceDataVersion=1.
After event creation, use Python's webbrowser module to open the event link in the default browser.
At the end, include a summary of how many events were created along with additional details.

IMPORTANT: Only use the following external dependencies when generating the code. Do not include any libraries or modules outside this list (e.g. dateutil) (aside from Python's standard library):

Flask>=2.0.0  
gunicorn  
google-auth-oauthlib>=0.4.6  
google-api-python-client>=2.70.0  
google-auth>=2.3.3  
Pillow>=9.0.0  
pytesseract>=0.3.10  
openai  
pvrecorder  
playsound==1.2.2  
IPython  
pytz  
tzlocal  
pdf2image  
docx2pdf  
python-dotenv  
requests>=2.25.0  
httplib2>=0.20.0  
uritemplate>=3.0.1  
oauthlib>=3.1.0  
six>=1.15.0  
Jinja2>=3.0.0  
MarkupSafe>=2.0.0  
itsdangerous>=2.0.0  
click>=8.0.0

SYSTEM DEPENDENCIES (use Homebrew on macOS):
- Tesseract OCR (for pytesseract) → install with:  `brew install tesseract`
- Poppler (for pdf2image) → install with:  `brew install poppler`
- LibreOffice (for docx2pdf, if Microsoft Word is not available) → install with:  `brew install --cask libreoffice`

Do not include code that requires dependencies outside of these or additional system dependencies. 

Also, here is how I have already authenticated earlier in my code (so credentials.json and token.json and SCOPES are already set and ready for you to use):

# Define the scope for Google Calendar API (read/write access)
SCOPES = ['https://www.googleapis.com/auth/calendar']

def create_credentials_file():
    credentials_data = {{
        "installed": {{
                "client_id": google_client_id,
                "project_id": google_project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": google_client_secret,
                "redirect_uris": [
                    "urn:ietf:wg:oauth:2.0:oob",
                    "http://localhost"
                ]
            }}
    }}
def authenticate():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        try:
            creds = flow.run_local_server(port=0)
        except Exception as e:
            print("Error launching local server for authentication, falling back to console input.")
            creds = flow.run_console()
        with open('token.json', 'w') as token_file:
            token_file.write(creds.to_json())
        print("token.json created successfully!")
    return creds

For example, if the user input is:
"team meeting next Friday at 2PM with a google meet conference call link"
Then generate Python code similar to the example below:

```python
import os
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone

import tzlocal
from googleapiclient.discovery import build

# Define the scope for Google Calendar API (read/write access)
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Assume that all these functions are defined earlier in the code you are adding to.
# They create the credentials file and perform authentication.
#
# def create_credentials_file():
#     credentials_data = {{
#         "installed": {{
                "client_id": google_client_id,
                "project_id": google_project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": google_client_secret,
                "redirect_uris": [
                    "urn:ietf:wg:oauth:2.0:oob",
                    "http://localhost"
                ]
            }}
#     }}
#
# def authenticate():
#     creds = None
#     if os.path.exists('token.json'):
#         creds = Credentials.from_authorized_user_file('token.json', SCOPES)
#     if not creds or not creds.valid:
#         flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
#         try:
#             creds = flow.run_local_server(port=0)
#         except Exception as e:
#             print("Error launching local server for authentication, falling back to console input.")
#             creds = flow.run_console()
#         with open('token.json', 'w') as token_file:
#             token_file.write(creds.to_json())
#         print("token.json created successfully!")
#     return creds

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

def create_calendar_event(creds, event_title, start_dt, end_dt, with_meet=False):
    '''Creates a Google Calendar event with an optional Google Meet link.'''
    service = build('calendar', 'v3', credentials=creds)
    local_tz = tzlocal.get_localzone()
    start_local = start_dt.astimezone(local_tz)
    end_local = end_dt.astimezone(local_tz)
    
    event = {{
        'summary': event_title,
        'description': 'Automatically created event via Calendar API.',
        'start': {{
            'dateTime': start_local.isoformat(),
            'timeZone': str(local_tz)
        }},
        'end': {{
            'dateTime': end_local.isoformat(),
            'timeZone': str(local_tz)
        }},
        'reminders': {{
            'useDefault': False,
            'overrides': [
                {{'method': 'popup', 'minutes': 10}},
                {{'method': 'popup', 'minutes': 60}},
                {{'method': 'popup', 'minutes': 1440}}
            ]
        }}
    }}
    
    if with_meet:
        event['conferenceData'] = {{
            'createRequest': {{
                'requestId': str(uuid.uuid4()),
                'conferenceSolutionKey': {{'type': 'hangoutsMeet'}}
            }}
        }}
    
    created_event = service.events().insert(
        calendarId='primary',
        body=event,
        conferenceDataVersion=1
    ).execute()
    
    print("Event created successfully!")
    print("Event link:", created_event.get('htmlLink'))
    webbrowser.open(created_event.get('htmlLink'))
    
    return created_event

def main():
    # Reuse your existing authentication function
    creds = authenticate()
    
    # Get current UTC time
    now = datetime.now(timezone.utc)
    
    # Example: set the event to start in 2 days at 2:00 PM local time (adjust as needed)
    start_time = now + timedelta(days=2, hours=14)
    end_time = start_time + timedelta(hours=1)
    
    event_title = "Team Meeting"
    created_event = create_calendar_event(creds, event_title, start_time, end_time, with_meet=True)
    print("Summary: 1 event created with title:", created_event.get('summary'))

if __name__ == "__main__":
    main()
    
Return only the Python code in a code block.
    """

    # Clear context files if they exist
    for file_name in ['gpt4oContext1.json', 'gpt4oMiniContext1.json']:
        if os.path.exists(file_name):
            with open(file_name, 'w') as f:
                json.dump({}, f)

    response_text = get_gpt4o_response(prompt)
    generated_code = extract_code(response_text)
    execution_output = ""
    if generated_code:
        try:
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                exec(generated_code, globals())
            execution_output = f.getvalue()
        except Exception as e:
            execution_output = f"Execution Error: {e}"

    return render_template("result.html",
                           combined_input=combined_input,
                           generated_code=generated_code,
                           execution_output=execution_output)

# ------------------------------
# Run the Flask App
# ------------------------------
if __name__ == "__main__":
    create_credentials_file()
    # Optionally force re-authentication (uncomment if needed)
    # authenticate()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))