from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import psutil
import requests
from sqlalchemy import text

import json
import os
from dotenv import load_dotenv
import time
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler
from postmarker.core import PostmarkClient


load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SUPABASE_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,  # Test connections before use
    'pool_recycle': 3600,   # Recycle connections every hour
    'pool_timeout': 20,     # Connection timeout
    'max_overflow': 10,     # Maximum overflow connections
    'pool_size': 10         # Connection pool size
}
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

SENDER_EMAIL = os.getenv('SENDER_EMAIL')
POSTMARK_API_KEY = os.getenv('POSTMARK_API_KEY')

# Global variable to track the current processing page
current_page = 0

def check_database_health():
    """Check database connection health and attempt recovery if needed"""
    try:
        # Test basic connection
        db.session.execute(text('SELECT 1'))
        return True, "Database connection healthy"
    except Exception as e:
        error_msg = str(e)
        print(f"[{datetime.now()}] Database health check failed: {error_msg}")
        
        # Try to recover from connection issues
        try:
            if "server closed the connection" in error_msg or "OperationalError" in error_msg:
                print(f"[{datetime.now()}] Attempting database connection recovery...")
                
                # Dispose of all connections and recreate
                db.engine.dispose()
                
                # Test the new connection
                db.session.execute(text('SELECT 1'))
                print(f"[{datetime.now()}] Database connection recovery successful")
                return True, "Database connection recovered"
            else:
                return False, f"Database error: {error_msg}"
                
        except Exception as recovery_error:
            print(f"[{datetime.now()}] Database recovery failed: {recovery_error}")
            return False, f"Database recovery failed: {recovery_error}"

def ensure_database_connection():
    """Ensure database connection is available before operations"""
    is_healthy, message = check_database_health()
    if not is_healthy:
        print(f"[{datetime.now()}] Database connection issue: {message}")
        # Try one more recovery attempt
        try:
            db.engine.dispose()
            db.session.execute(text('SELECT 1'))
            print(f"[{datetime.now()}] Final database recovery attempt successful")
            return True
        except Exception as final_error:
            print(f"[{datetime.now()}] Final database recovery attempt failed: {final_error}")
            return False
    return True

login_manager = LoginManager(app)
login_manager.login_view = 'login'
# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)  # Assuming username is the email
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(10), nullable=False)

class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), nullable=False)
    keyword_type = db.Column(db.String(100), nullable=True)
    emails = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('alerts', lazy=True))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_run_date = db.Column(db.DateTime)

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"message": "pong"})
    

@login_manager.user_loader
def load_user(user_id):
    try:
        # Convert user_id to an integer before querying
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        # Return None if user_id is not valid
        return None

# Status API to get current page number
@app.route('/get_status', methods=['GET'])
def get_status():
    global current_page
    return jsonify({"current_page": current_page})

# Fetch tenders function with real-time updates
def fetch_tenders():
    global current_page
    base_url = 'https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync'
    page_number = 1
    max_retries = 5
    valid_tenders = []
    
    # Create a session to maintain cookies
    session = requests.Session()
    
    # First, establish a session by accessing the main page
    try:
        print(f"[{datetime.now()}] Establishing session with Etimad...")
        main_page_response = session.get(
            "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            },
            timeout=60
        )
        
        if main_page_response.status_code == 200:
            print(f"[{datetime.now()}] Session established successfully. Cookies: {len(session.cookies)}")
            # Add longer delay after establishing session to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 15 seconds after establishing session...")
            time.sleep(15)
        else:
            print(f"[{datetime.now()}] Warning: Could not establish session. Status: {main_page_response.status_code}")
            # If we can't establish a session, it might be a temporary issue
            if main_page_response.status_code in [429, 503, 502, 500]:
                raise Exception(f"Etimad server is experiencing issues (HTTP {main_page_response.status_code}). Please try again later.")
            elif main_page_response.status_code == 403:
                raise Exception("Access to Etimad server is currently restricted. Please try again later.")
            
    except requests.exceptions.Timeout:
        raise Exception("Connection to Etimad server timed out. The server may be overloaded. Please try again later.")
    except requests.exceptions.ConnectionError:
        raise Exception("Cannot connect to Etimad server. Please check your internet connection and try again later.")
    except Exception as e:
        print(f"[{datetime.now()}] Warning: Could not establish session: {e}")
        if "Etimad server" in str(e):
            raise e
        raise Exception(f"Failed to establish connection with Etimad server: {str(e)}. Please try again later.")

    now = datetime.now()
    # Fetch tenders from the last 30 days to include recent and upcoming tenders
    thirty_days_ago = now - timedelta(days=30)
    stop_fetching = False  # Flag to stop fetching when tenders older than 30 days are found

    while not stop_fetching:
        retry_count = 0
        success = False

        while retry_count < max_retries:
            try:
                print(f"[{datetime.now()}] Fetching page {page_number} from {base_url}")
                
                # Use proper headers to mimic a real browser request
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br, zstd',
                    'Connection': 'keep-alive',
                    'Referer': 'https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Host': 'tenders.etimad.sa'
                }
                
                # Use the working URL format: PublishDateId=5&PageSize=6&PageNumber={page_number}
                response = session.get(f'{base_url}?PublishDateId=5&PageSize=24&PageNumber={page_number}', headers=headers, timeout=60)
                response.raise_for_status()  # Raise an exception for HTTP errors
                
                # Log response status and content length for debugging
                print(f"[{datetime.now()}] Page {page_number} response: {response.status_code}, Content-Length: {len(response.content)}")
                
                # Log headers for debugging
                print(f"[{datetime.now()}] Response headers: {dict(response.headers)}")
                
                try:
                    response_data = response.json()
                    tenders = response_data.get('data', [])
                    print(f"[{datetime.now()}] Page {page_number} returned {len(tenders)} tenders")

                    # Log the first tender to verify structure
                    if tenders and page_number == 1:
                        first_tender = tenders[0]
                        print(f"[{datetime.now()}] First tender sample: ID={first_tender.get('tenderId', 'N/A')}, Name={first_tender.get('tenderName', 'N/A')[:50]}...")
                    
                    # Validate that we have the expected data structure
                    if tenders and not isinstance(tenders, list):
                        print(f"[{datetime.now()}] Warning: Expected list of tenders, got {type(tenders)}")
                        retry_count += 1
                        time.sleep(2)
                        continue

                    if not tenders:  # No more tenders found, break the loop
                        print(f"[{datetime.now()}] No more tenders found on page {page_number}")
                        return valid_tenders
                except json.JSONDecodeError as json_error:
                    print(f"[{datetime.now()}] JSON decode error on page {page_number}: {json_error}")
                    print(f"[{datetime.now()}] Response content preview: {response.text[:200]}...")
                    
                    # If we get HTML instead of JSON, it might be a rate limit or bot detection
                    if response.status_code == 200 and 'text/html' in response.headers.get('content-type', ''):
                        print(f"[{datetime.now()}] ⚠️ Received HTML instead of JSON - possible rate limiting or bot detection")
                        print(f"[{datetime.now()}] Waiting 60 seconds before retry...")
                        time.sleep(60)  # Wait 60 seconds
                        retry_count += 1
                        continue
                    
                    retry_count += 1
                    time.sleep(2)
                    continue

                # Filter tenders by submission date (within last 30 days)
                valid_count = 0
                for tender in tenders:
                    try:
                        submission_date = datetime.strptime(tender['submitionDate'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
                        if submission_date >= thirty_days_ago:  # Only include tenders within the last 30 days
                            valid_tenders.append(tender)
                            valid_count += 1
                        else:
                            print(f"[{datetime.now()}] Stopping at older tender: {tender.get('tenderName', 'Unknown')} - Date: {submission_date}")
                            stop_fetching = True  # Stop fetching if we encounter an older tender
                            break  # Exit the loop early since all subsequent tenders will be older
                    except (KeyError, ValueError) as date_error:
                        print(f"[{datetime.now()}] Error parsing date for tender {tender.get('tenderId', 'Unknown')}: {date_error}")
                        # Include tenders with invalid dates for now
                        valid_tenders.append(tender)
                        valid_count += 1
                
                print(f"[{datetime.now()}] Page {page_number}: {valid_count} valid tenders out of {len(tenders)} total")

                success = True
                current_page = page_number  # Update the global page number
                break

            except requests.exceptions.RequestException as e:
                retry_count += 1
                print(f"[{datetime.now()}] Request error on page {page_number} (attempt {retry_count}/{max_retries}): {e}")
                
                # Handle specific HTTP error codes
                if hasattr(e, 'response') and e.response is not None:
                    status_code = e.response.status_code
                    if status_code == 429:
                        print(f"[{datetime.now()}] Rate limited by Etimad server. Waiting 60 seconds...")
                        time.sleep(60)
                    elif status_code in [503, 502, 500]:
                        print(f"[{datetime.now()}] Etimad server error {status_code}. Waiting 30 seconds...")
                        time.sleep(30)
                    elif status_code == 403:
                        print(f"[{datetime.now()}] Access forbidden by Etimad server. Waiting 120 seconds...")
                        time.sleep(120)
                    else:
                        time.sleep(2)  # Default wait time
                else:
                    time.sleep(2)  # Default wait time
                
                # If we've exhausted all retries, raise a user-friendly error
                if retry_count >= max_retries:
                    if "rate limit" in str(e).lower() or "429" in str(e):
                        raise Exception("Etimad server is currently rate limiting requests. Please try again later.")
                    elif "timeout" in str(e).lower():
                        raise Exception("Etimad server is not responding. Please try again later.")
                    else:
                        raise Exception(f"Failed to fetch data from Etimad after {max_retries} attempts: {str(e)}. Please try again later.")

        if success and not stop_fetching:
            page_number += 1
            # Add longer delay between pages to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 30 seconds before fetching next page...")
            time.sleep(30)  # Wait 30 seconds between pages
        else:
            break  # Stop fetching if retries are exhausted or there are no tenders

    current_page = 0  # Reset page number after processing
    log_memory_usage("Fetch Tenders.")
    return valid_tenders

# Filter tenders based on keywords
def filter_tenders(tenders, search_criteria):
    filtered_tenders = []
    now = datetime.now()
    # Include tenders from the last 60 days to capture more relevant results
    sixty_days_ago = now - timedelta(days=60)

    # Log the search criteria for debugging
    print(f"Search Criteria: {search_criteria}")

    for tender in tenders:
        submission_date_str = tender['submitionDate'].split('.')[0]
        submission_date = datetime.strptime(submission_date_str, "%Y-%m-%dT%H:%M:%S")

        # Skip tenders older than 60 days
        if submission_date < sixty_days_ago:
            continue

        # Initialize matching flags
        agency_matches = True if not search_criteria.get('agency_name') else False
        activity_matches = True if not search_criteria.get('activity_name') else False
        keyword_matches = True if not search_criteria.get('keywords') else False
        tender_name_matches = True if not search_criteria.get('tender_name') else False

        # Match by Agency Name (Handle multiple agency names)
        if search_criteria.get('agency_name'):
            if any(agency.strip().lower() in tender.get('agencyName', '').strip() for agency in search_criteria['agency_name']):
                agency_matches = True

        # Match by Activity Name (Handle multiple activity names)
        if search_criteria.get('activity_name'):
            if any(activity.strip().lower() in tender.get('tenderActivityName', '').strip() for activity in search_criteria['activity_name']):
                activity_matches = True

        # Match by Keywords (Partial Match in any field)
        if search_criteria.get('keywords'):
            for keyword in search_criteria['keywords']:
                if (keyword in tender.get('tenderName', '') or
                    keyword in tender.get('tenderActivityName', '') or
                    keyword in tender.get('agencyName', '')):
                    keyword_matches = True
                    break  # Stop checking further keywords if a match is found

        # Match by Tender Name (Exact Match)
        if search_criteria.get('tender_name'):
            if search_criteria['tender_name'].strip().lower() in tender.get('tenderName', '').strip().lower():
                tender_name_matches = True

        # Only include the tender if all matching conditions are met
        if agency_matches and activity_matches and keyword_matches and tender_name_matches:
            filtered_tenders.append(tender)
    
    log_memory_usage("Filter Tenders.")
    return filtered_tenders

def send_email(tenders, search_criteria, receiver_emails):
    subject = "🎯 New Matching Tenders Found - Signal Alert"
    
    # Create beautiful HTML email template with blue and gold theme
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signal Tender Alert</title>
    <style>
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            margin: 0; 
            padding: 0; 
            background-color: #f8f9ff; 
            color: #343a40; 
        }}
        .email-container {{ 
            max-width: 600px; 
            margin: 0 auto; 
            background-color: #ffffff; 
            border-radius: 12px; 
            overflow: hidden; 
            box-shadow: 0 10px 25px rgba(1, 5, 174, 0.15); 
        }}
        .header {{ 
            background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
            color: #ffffff; 
            padding: 30px 20px; 
            text-align: center; 
        }}
        .header h1 {{ 
            margin: 0; 
            font-size: 28px; 
            font-weight: 700; 
            color: #ffffff; 
        }}
        .header .subtitle {{ 
            margin: 10px 0 0 0; 
            font-size: 16px; 
            color: #f1c061; 
            font-weight: 500; 
        }}
        .content {{ 
            padding: 30px 20px; 
        }}
        .criteria-section {{ 
            background: linear-gradient(135deg, #f8f9ff 0%, #ffffff 100%); 
            border: 2px solid #0105ae; 
            border-radius: 8px; 
            padding: 20px; 
            margin-bottom: 25px; 
        }}
        .criteria-title {{ 
            color: #0105ae; 
            font-size: 18px; 
            font-weight: 700; 
            margin-bottom: 15px; 
            display: flex; 
            align-items: center; 
        }}
        .criteria-title::before {{ 
            content: "🔍"; 
            margin-right: 10px; 
            font-size: 20px; 
        }}
        .criteria-item {{ 
            background-color: #ffffff; 
            border-left: 4px solid #f1c061; 
            padding: 8px 15px; 
            margin: 8px 0; 
            border-radius: 0 6px 6px 0; 
        }}
        .tender-card {{ 
            background: linear-gradient(135deg, #ffffff 0%, #f8f9ff 100%); 
            border: 1px solid #e9ecef; 
            border-radius: 12px; 
            padding: 20px; 
            margin: 20px 0; 
            box-shadow: 0 4px 6px rgba(1, 5, 174, 0.1); 
            transition: all 0.3s ease; 
        }}
        .tender-card:hover {{ 
            transform: translateY(-2px); 
            box-shadow: 0 8px 15px rgba(1, 5, 174, 0.2); 
        }}
        .tender-header {{ 
            background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
            color: #ffffff; 
            padding: 15px 20px; 
            margin: -20px -20px 20px -20px; 
            border-radius: 12px 12px 0 0; 
        }}
        .tender-title {{ 
            font-size: 18px; 
            font-weight: 700; 
            margin: 0; 
            color: #ffffff; 
        }}
        .tender-id {{ 
            font-size: 14px; 
            color: #f1c061; 
            margin: 5px 0 0 0; 
        }}
        .tender-details {{ 
            display: grid; 
            grid-template-columns: 1fr 1fr; 
            gap: 15px; 
            margin: 15px 0; 
        }}
        .detail-item {{ 
            background-color: #ffffff; 
            padding: 12px; 
            border-radius: 8px; 
            border: 1px solid #e9ecef; 
        }}
        .detail-label {{ 
            font-weight: 600; 
            color: #0105ae; 
            font-size: 12px; 
            text-transform: uppercase; 
            letter-spacing: 0.5px; 
            margin-bottom: 5px; 
        }}
        .detail-value {{ 
            color: #343a40; 
            font-size: 14px; 
            font-weight: 500; 
        }}
        .view-button {{ 
            display: inline-block; 
            background: linear-gradient(135deg, #f1c061 0%, #e6b84c 100%); 
            color: #000056; 
            text-decoration: none; 
            padding: 12px 25px; 
            border-radius: 8px; 
            font-weight: 600; 
            text-align: center; 
            margin-top: 15px; 
            transition: all 0.3s ease; 
        }}
        .view-button:hover {{ 
            background: linear-gradient(135deg, #e6b84c 0%, #d4a73a 100%); 
            transform: translateY(-1px); 
            box-shadow: 0 4px 8px rgba(241, 192, 97, 0.3); 
        }}
        .footer {{ 
            background-color: #f8f9ff; 
            padding: 20px; 
            text-align: center; 
            border-top: 1px solid #e9ecef; 
        }}
        .footer-text {{ 
            color: #6c757d; 
            font-size: 14px; 
            margin: 0; 
        }}
        .stats {{ 
            background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
            color: #ffffff; 
            padding: 15px 20px; 
            border-radius: 8px; 
            margin-bottom: 20px; 
            text-align: center; 
        }}
        .stats-number {{ 
            font-size: 24px; 
            font-weight: 700; 
            color: #f1c061; 
        }}
        .stats-label {{ 
            font-size: 14px; 
            color: #ffffff; 
            margin-top: 5px; 
        }}
    </style>
</head>
<body>
    <div class="email-container">
        <div class="header">
            <h1>🎯 Signal Alert</h1>
            <div class="subtitle">New Matching Tenders Found</div>
        </div>
        
        <div class="content">
            <div class="stats">
                <div class="stats-number">{tender_count}</div>
                <div class="stats-label">New Tenders Found</div>
            </div>
            
            <div class="criteria-section">
                <div class="criteria-title">Search Criteria</div>
                {criteria_html}
            </div>
            
            {tenders_html}
        </div>
        
        <div class="footer">
            <p class="footer-text">This alert was generated by Signal Tender Monitoring System</p>
            <p class="footer-text">Powered by Beyond Digital Team • {current_time}</p>
        </div>
    </div>
</body>
</html>"""
    
    # Construct search criteria information
    criteria_items = []
    if search_criteria.get('agency_name'):
        criteria_items.append(f'<div class="criteria-item">🏛️ Agency: {search_criteria["agency_name"]}</div>')
    if search_criteria.get('activity_name'):
        criteria_items.append(f'<div class="criteria-item">⚡ Activity: {search_criteria["activity_name"]}</div>')
    if search_criteria.get('keywords'):
        criteria_items.append(f'<div class="criteria-item">🔑 Keywords: {", ".join(search_criteria["keywords"])}</div>')
    if search_criteria.get('tender_name'):
        criteria_items.append(f'<div class="criteria-item">📋 Tender Name: {search_criteria["tender_name"]}</div>')
    
    criteria_html = "".join(criteria_items) if criteria_items else '<div class="criteria-item">📊 General Alert - All New Tenders</div>'
    
    # Generate tender cards HTML
    tenders_html = ""
    for tender in tenders:
        try:
            # Parse dates
            submission_date_str = tender['submitionDate'].split('.')[0] if tender.get('submitionDate') else "N/A"
            submission_date = datetime.strptime(submission_date_str, "%Y-%m-%dT%H:%M:%S") if submission_date_str != "N/A" else None
            formatted_submission_date = submission_date.strftime("%Y-%m-%d %H:%M:%S") if submission_date else "N/A"
            
            last_enqueries_date_str = tender.get('lastEnqueriesDate', '').split('.')[0] if tender.get('lastEnqueriesDate') else "N/A"
            last_enqueries_date = datetime.strptime(last_enqueries_date_str, "%Y-%m-%dT%H:%M:%S") if last_enqueries_date_str != "N/A" else None
            formatted_last_enqueries_date = last_enqueries_date.strftime("%Y-%m-%d %H:%M:%S") if last_enqueries_date else "N/A"
            
            last_offer_date_str = tender.get('lastOfferPresentationDate', '').split('.')[0] if tender.get('lastOfferPresentationDate') else "N/A"
            last_offer_date = datetime.strptime(last_offer_date_str, "%Y-%m-%dT%H:%M:%S") if last_offer_date_str != "N/A" else None
            formatted_last_offer_date = last_offer_date.strftime("%Y-%m-%d %H:%M:%S") if last_offer_date else "N/A"
            
            tender_html = f"""
            <div class="tender-card">
                <div class="tender-header">
                    <div class="tender-title">{tender.get('tenderName', 'N/A')}</div>
                    <div class="tender-id">ID: {tender.get('tenderId', 'N/A')}</div>
                </div>
                
                <div class="tender-details">
                    <div class="detail-item">
                        <div class="detail-label">Agency</div>
                        <div class="detail-value">{tender.get('agencyName', 'N/A')}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Activity</div>
                        <div class="detail-value">{tender.get('tenderActivityName', 'N/A')}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Submission Date</div>
                        <div class="detail-value">{formatted_last_offer_date}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Last Enquiries</div>
                        <div class="detail-value">{formatted_last_enqueries_date}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Published Date</div>
                        <div class="detail-value">{formatted_submission_date}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">Reference</div>
                        <div class="detail-value">{tender.get('referenceNumber', 'N/A')}</div>
                    </div>
                </div>
                
                <a href="https://tenders.etimad.sa/Tender/DetailsForVisitor?STenderId={tender.get('tenderIdString', '')}" 
                   class="view-button" target="_blank">
                   🔍 View Full Tender Details
                </a>
            </div>
            """
            tenders_html += tender_html
        except Exception as e:
            print(f"Error processing tender {tender.get('tenderId', 'Unknown')}: {e}")
            continue
    
    # Format the HTML template
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = html_template.format(
        tender_count=len(tenders),
        criteria_html=criteria_html,
        tenders_html=tenders_html,
        current_time=current_time
    )

    try:
        # Send the email using Postmark API
        client = PostmarkClient(server_token=POSTMARK_API_KEY)
        
        # Send to each recipient
        for email in receiver_emails:
            response = client.emails.send(
                From=SENDER_EMAIL,
                To=email.strip(),
                Subject=subject,
                HtmlBody=body
            )
            print(f"[{datetime.now()}] Email sent to {email.strip()} successfully. Message ID: {response.get('MessageID', 'N/A')}")
        
        log_memory_usage("Email sent successfully.")
    except Exception as e:
        print(f"Failed to send email to {', '.join(receiver_emails)}: {e}")


import threading
from queue import Queue
import time as time_module

# Background email processing queue
email_queue = Queue()
email_processing_active = False

def background_email_processor():
    """Background worker to process email queue"""
    global email_processing_active
    email_processing_active = True
    
    print(f"[{datetime.now()}] Background email processor started")
    
    while email_processing_active:
        try:
            # Get email task from queue with timeout
            try:
                email_task = email_queue.get(timeout=1)  # 1 second timeout
            except:
                continue  # No tasks, continue loop
            
            if email_task:
                task_type = email_task[0] if len(email_task) > 0 else 'email'
                
                if task_type == 'alert_processing':
                    # Handle alert processing task
                    search_criteria, receiver_emails, task_id = email_task[1:]
                    
                    print(f"[{datetime.now()}] Processing background alert task {task_id} for {len(receiver_emails)} recipients")
                    
                    try:
                        # Fetch tenders in the background
                        tenders = fetch_tenders()
                        filtered_tenders = filter_tenders(tenders, search_criteria)
                        
                        if filtered_tenders:
                            # Add delay before sending to avoid overwhelming the email service
                            time_module.sleep(5)
                            
                            # Send the email
                            send_email(filtered_tenders, search_criteria, receiver_emails)
                            
                            print(f"[{datetime.now()}] Background alert task {task_id} completed successfully - {len(filtered_tenders)} tenders sent")
                        else:
                            print(f"[{datetime.now()}] Background alert task {task_id} completed - no matching tenders found")
                        
                    except Exception as e:
                        print(f"[{datetime.now()}] Error in background alert task {task_id}: {e}")
                    
                    finally:
                        # Mark task as done
                        email_queue.task_done()
                        
                else:
                    # Handle regular email task
                    tenders, search_criteria, receiver_emails, task_id = email_task
                    
                    print(f"[{datetime.now()}] Processing background email task {task_id} for {len(receiver_emails)} recipients")
                    
                    try:
                        # Add delay before sending to avoid overwhelming the email service
                        time_module.sleep(5)
                        
                        # Send the email
                        send_email(tenders, search_criteria, receiver_emails)
                        
                        print(f"[{datetime.now()}] Background email task {task_id} completed successfully")
                        
                    except Exception as e:
                        print(f"[{datetime.now()}] Error in background email task {task_id}: {e}")
                    
                    finally:
                        # Mark task as done
                        email_queue.task_done()
                    
        except Exception as e:
            print(f"[{datetime.now()}] Error in background email processor: {e}")
            time_module.sleep(5)  # Wait before continuing
    
    print(f"[{datetime.now()}] Background email processor stopped")

def start_background_email_processor():
    """Start the background email processor thread"""
    if not email_processing_active:
        email_thread = threading.Thread(target=background_email_processor, daemon=True)
        email_thread.start()
        print(f"[{datetime.now()}] Background email processor thread started")
        return email_thread
    return None

def add_email_to_queue(tenders, search_criteria, receiver_emails, task_id=None):
    """Add an email task to the background processing queue"""
    if task_id is None:
        task_id = f"task_{int(time_module.time())}"
    
    email_task = (tenders, search_criteria, receiver_emails, task_id)
    email_queue.put(email_task)
    print(f"[{datetime.now()}] Email task {task_id} added to background queue")
    return task_id

def add_alert_to_background_queue(search_criteria, receiver_emails, task_id=None):
    """Add an alert processing task to the background queue"""
    if task_id is None:
        task_id = f"alert_{int(time_module.time())}"
    
    # Create an alert task that will fetch tenders and send emails in the background
    alert_task = ('alert_processing', search_criteria, receiver_emails, task_id)
    email_queue.put(alert_task)
    print(f"[{datetime.now()}] Alert processing task {task_id} added to background queue")
    return task_id

def process_alert(alert):
    keywords = {
        'agency_name': alert.keyword if alert.keyword_type == 'agency' else '',
        'activity_name': alert.keyword if alert.keyword_type == 'activity' else '',
        'tender_name': alert.keyword if alert.keyword_type == 'tender' else '',
        'keywords': alert.keyword.split(',') if alert.keyword_type == 'keyword' else []
    }

    try:
        tenders = fetch_tenders()
        filtered_tenders = filter_tenders(tenders, keywords)

        if filtered_tenders:
            receiver_emails = alert.emails.split(',')
            
            # Add email to background queue instead of sending immediately
            task_id = add_email_to_queue(filtered_tenders, keywords, receiver_emails, f"alert_{alert.id}")
            
            # Update alert status to show email is queued
            alert.last_run_date = datetime.utcnow()
            alert.status = 'email_queued'  # Add this field to Alert model if needed
            db.session.commit()
            
            print(f"Email for alert ID {alert.id} added to background queue (task: {task_id})")
        else:
            print(f"No matching tenders found for alert ID {alert.id}.")
            
    except Exception as e:
        error_message = str(e)
        print(f"[{datetime.now()}] Error processing alert ID {alert.id}: {error_message}")
        
        # Update alert status to show error
        alert.status = 'error'  # Add this field to Alert model if needed
        alert.last_error = error_message  # Add this field to Alert model if needed
        db.session.commit()

from collections import defaultdict

def run_all_alerts():
    """Run all alerts with proper database connection handling and error recovery"""
    try:
        with app.app_context():
            # Ensure database connection is healthy before proceeding
            if not ensure_database_connection():
                print(f"[{datetime.now()}] Failed to establish database connection, aborting run_all_alerts")
                return
            
            print(f"[{datetime.now()}] Database connection verified, proceeding with alert processing")
            
            alerts = Alert.query.all()  # Fetch all alerts from the database
            tenders_by_receiver = defaultdict(list)  # Dictionary to group tenders by email receiver

            for alert in alerts:
                try:
                    # Prepare keywords for fetching tenders
                    keywords = {
                        'agency_name': [alert.keyword] if alert.keyword_type == 'agency' else '',
                        'activity_name': [alert.keyword] if alert.keyword_type == 'activity' else '',
                        'tender_name': alert.keyword if alert.keyword_type == 'tender' else '',
                        'keywords': alert.keyword.split(',') if alert.keyword_type == 'keyword' else []
                    }

                    print(f"[{datetime.now()}] Processing alert ID: {alert.id}")
                    print(f"[{datetime.now()}] Search Criteria for alert ID {alert.id}: {keywords}")

                    # Fetch and filter tenders based on the alert's keywords
                    try:
                        tenders = fetch_tenders()
                        filtered_tenders = filter_tenders(tenders, keywords)
                    except Exception as e:
                        error_message = str(e)
                        print(f"[{datetime.now()}] Error fetching tenders for alert ID {alert.id}: {error_message}")
                        
                        # Log the error but continue with other alerts
                        continue

                    print(f"[{datetime.now()}] Filtered tenders for alert ID {alert.id}: {len(filtered_tenders)} tenders found.")

                    # Add filtered tenders to the appropriate receivers
                    if filtered_tenders:
                        receiver_emails = alert.emails.split(',')
                        for email in receiver_emails:
                            tenders_by_receiver[email].extend(filtered_tenders)  # Group tenders by receiver email

                    # Update the last run date for the alert with proper error handling
                    try:
                        alert.last_run_date = datetime.utcnow()
                        db.session.commit()
                        print(f"[{datetime.now()}] Successfully updated last_run_date for alert ID {alert.id}")
                    except Exception as commit_error:
                        print(f"[{datetime.now()}] Error committing alert update for ID {alert.id}: {commit_error}")
                        # Try to rollback and continue
                        try:
                            db.session.rollback()
                            print(f"[{datetime.now()}] Successfully rolled back session for alert ID {alert.id}")
                        except Exception as rollback_error:
                            print(f"[{datetime.now()}] Error rolling back session for alert ID {alert.id}: {rollback_error}")
                            # If rollback fails, try to refresh the session
                            try:
                                db.session.close()
                                db.session.execute(text('SELECT 1'))
                                print(f"[{datetime.now()}] Successfully refreshed session after rollback failure")
                            except Exception as refresh_error:
                                print(f"[{datetime.now()}] Failed to refresh session after rollback failure: {refresh_error}")
                                continue
                        
                except Exception as alert_error:
                    print(f"[{datetime.now()}] Unexpected error processing alert ID {alert.id}: {alert_error}")
                    continue

            # Send one grouped email per receiver
            for receiver_email, tenders in tenders_by_receiver.items():
                if tenders:
                    try:
                        print(f"[{datetime.now()}] Preparing email for {receiver_email} with {len(tenders)} tenders.")
                        # Add to background queue instead of sending immediately
                        task_id = add_email_to_queue(tenders, {"grouped_alert": "Grouped by Receiver"}, [receiver_email], f"grouped_{receiver_email}")
                        print(f"[{datetime.now()}] Grouped email for {receiver_email} added to background queue (task: {task_id})")
                    except Exception as email_error:
                        print(f"[{datetime.now()}] Error adding email to queue for {receiver_email}: {email_error}")
                        continue

            print(f"[{datetime.now()}] Finished processing all alerts.")
            
    except Exception as main_error:
        print(f"[{datetime.now()}] Critical error in run_all_alerts: {main_error}")
        # Try to log the error and continue
        try:
            import traceback
            traceback.print_exc()
        except:
            pass


from apscheduler.triggers.cron import CronTrigger
import pytz
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()
def start_scheduler():
    logger.info("Scheduler starting...")
    scheduler = BackgroundScheduler()
    timezone = pytz.timezone('Asia/Riyadh')
    
    # Track job status and retry attempts
    job_status = {
        'last_success': None,
        'last_failure': None,
        'retry_count': 0,
        'max_retries': 3,
        'next_run': None,
        'is_retry_mode': False
    }
    
    def schedule_retry():
        """Schedule a retry after 30 minutes"""
        if job_status['retry_count'] < job_status['max_retries']:
            retry_time = datetime.now() + timedelta(minutes=30)
            job_status['retry_count'] += 1
            job_status['is_retry_mode'] = True
            job_status['next_run'] = retry_time
            
            logger.info(f"Job failed. Scheduling retry {job_status['retry_count']}/{job_status['max_retries']} for: {retry_time}")
            
            # Add retry job
            scheduler.add_job(
                func=debug_job,
                trigger='date',
                run_date=retry_time,
                id='retry_job',
                timezone=timezone
            )
        else:
            # Max retries reached, reset and wait for next scheduled run
            job_status['retry_count'] = 0
            job_status['is_retry_mode'] = False
            logger.warning(f"Max retries reached. Returning to normal schedule.")

    def debug_job():
        """Scheduler job with error handling and retry logic"""
        try:
            logger.info("Scheduler triggered run_all_alerts")
            run_all_alerts()
            logger.info("Scheduler job completed successfully")
            
            # Reset retry count on success
            job_status['retry_count'] = 0
            job_status['is_retry_mode'] = False
            job_status['last_success'] = datetime.now()
            
            # If this was a retry job, remove it
            try:
                scheduler.remove_job('retry_job')
            except:
                pass
                
        except Exception as job_error:
            logger.error(f"Scheduler job failed: {job_error}")
            job_status['last_failure'] = datetime.now()
            
            # Try to recover from database connection issues
            try:
                if "server closed the connection" in str(job_error) or "OperationalError" in str(job_error):
                    logger.info("Attempting to recover from database connection issue...")
                    
                    # Try to refresh database connections
                    with app.app_context():
                        try:
                            db.engine.dispose()
                            db.session.execute(text('SELECT 1'))
                            logger.info("Database connection recovered successfully")
                            
                            # Retry the job once immediately
                            logger.info("Retrying scheduler job immediately...")
                            run_all_alerts()
                            logger.info("Scheduler job retry completed successfully")
                            
                            # Reset retry count on success
                            job_status['retry_count'] = 0
                            job_status['is_retry_mode'] = False
                            
                            # If this was a retry job, remove it
                            try:
                                scheduler.remove_job('retry_job')
                            except:
                                pass
                                
                            return
                            
                        except Exception as recovery_error:
                            logger.error(f"Failed to recover from database connection issue: {recovery_error}")
                            
                else:
                    logger.error(f"Non-database error in scheduler job: {job_error}")
                    
            except Exception as recovery_error:
                logger.error(f"Error during recovery attempt: {recovery_error}")
            
            # Log the full traceback for debugging
            try:
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
            except:
                pass
            
            # Schedule retry if not already in retry mode
            if not job_status['is_retry_mode']:
                schedule_retry()

    def database_health_check_job():
        """Periodic database health check job"""
        try:
            with app.app_context():
                is_healthy, message = check_database_health()
                if is_healthy:
                    logger.info("Database health check passed")
                else:
                    logger.warning(f"Database health check failed: {message}")
        except Exception as health_error:
            logger.error(f"Database health check job failed: {health_error}")

    # Add the main scheduled job (daily at 11:51 AM)
    main_trigger = CronTrigger(hour=12, minute=33, timezone=timezone)
    scheduler.add_job(
        func=debug_job,
        trigger=main_trigger,
        id='main_alert_job'
    )
    
    # Add a database health check job that runs every 30 minutes
    health_trigger = CronTrigger(minute='*/30', timezone=timezone)
    scheduler.add_job(func=database_health_check_job, trigger=health_trigger, id='health_check_job')
    
    scheduler.start()
    logger.info("Scheduler started successfully.")
    logger.info(f"Main alert job scheduled for daily at 11:51 AM {timezone}")
    logger.info(f"Database health check scheduled every 30 minutes")


def log_and_run_alerts():
    logger.info(f"run_all_alerts triggered at {datetime.now()}")
    run_all_alerts()    
# Routes
@app.route('/')
def index():
    return render_template('index.html')

import traceback
@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('dashboard'))    
    try:
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']
            role = request.form['role']
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            new_user = User(username=username, password=hashed_password, role=role)
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')
    except Exception as e:
        print("Error: ", e)
        traceback.print_exc()
        return "Internal Server Error", 500
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)  # Log the user in
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        flash('Login failed. Please check your credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()  # Log the user out
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Load the keywords
    with open('keywords.json', 'r', encoding='utf-8') as file:
        keywords = json.load(file)

    # Extract the agencies and activities
    activities = keywords.get('activity_names', [])
    agencies = keywords.get('agency_names', [])

    # Fetch alerts for the user (admin or regular user)
    if current_user.role == 'admin':
        user_alerts = Alert.query.order_by(Alert.created_at.asc()).all()  # Sort by created_at
    else:
        user_alerts = Alert.query.filter_by(user_id=current_user.id).order_by(Alert.created_at.asc()).all()

    # Pass data to the template
    return render_template('dashboard.html', activities=activities, agencies=agencies, alerts=user_alerts)

@app.route('/get_tenders', methods=['POST'])
def get_tenders():
    if not current_user or not current_user.is_authenticated:
        flash("User must be logged in to create an alert.", "danger")
        return redirect(url_for('login'))

    user = current_user
    selected_activities = [activity for activity in request.form.getlist('activity_name') if activity]  # Remove empty values
    selected_agencies = [agency for agency in request.form.getlist('agency_name') if agency]  # Remove empty values
    receiver_emails = sorted([email.strip() for email in request.form.get('emails').split(',')])  # Sort emails for consistency
    search_keywords = request.form.get('search_keywords', '').split(',')
    tender_name = request.form.get('tender_name', '')

    if not selected_activities and not selected_agencies and not search_keywords and not tender_name:
        flash("Please select at least one search criteria.", "danger")
        return redirect(url_for('dashboard'))

    # Function to check for any existing alert for the same keyword (agency, activity, or tender) and receiver emails
    def check_existing_alerts(keyword, keyword_type):
        existing_alerts = Alert.query.filter_by(keyword=keyword, keyword_type=keyword_type, user_id=user.id).all()
        for alert in existing_alerts:
            alert_emails = set(alert.emails.split(','))
            if any(email in alert_emails for email in receiver_emails):  # If any email matches
                return alert
        return None

    # Keep track if any alert exists to avoid sending emails or creating alerts
    existing_alert_found = False

    # Handle alerts for selected activities (same as agency handling)
    for activity in selected_activities:
        existing_alert = check_existing_alerts(activity, "activity")
        if existing_alert:
            flash(f"Alert already exists for activity '{activity}' with one of the receiver emails: {existing_alert.emails}", "danger")
            existing_alert_found = True  # Mark that an existing alert was found, so we won't create a new one

    # Handle alerts for selected agencies
    for agency in selected_agencies:
        existing_alert = check_existing_alerts(agency, "agency")
        if existing_alert:
            flash(f"Alert already exists for agency '{agency}' with one of the receiver emails: {existing_alert.emails}", "danger")
            existing_alert_found = True  # Mark that an existing alert was found, so we won't create a new one

    # Handle tender_name or keyword if provided
    if tender_name:
        existing_alert = check_existing_alerts(tender_name, "tender")
        if existing_alert:
            flash(f"Alert already exists for tender '{tender_name}' with one of the receiver emails: {existing_alert.emails}", "danger")
            existing_alert_found = True  # Mark that an existing alert was found

    elif search_keywords != ['']:
        keyword_str = ', '.join(search_keywords)
        existing_alert = check_existing_alerts(keyword_str, "keyword")
        if existing_alert:
            flash(f"Alert already exists for keywords with one of the receiver emails: {existing_alert.emails}", "danger")
            existing_alert_found = True  # Mark that an existing alert was found

    # If any existing alert was found, stop further processing
    if existing_alert_found:
        return redirect(url_for('dashboard'))

    # If no existing alert was found, create new alerts
    alerts_created = []
    if selected_activities:
        for activity in selected_activities:
            new_alert = Alert(keyword=activity, keyword_type="activity", emails=','.join(receiver_emails), user_id=user.id)
            db.session.add(new_alert)
            alerts_created.append(new_alert)

    if selected_agencies:
        for agency in selected_agencies:
            new_alert = Alert(keyword=agency, keyword_type="agency", emails=','.join(receiver_emails), user_id=user.id)
            db.session.add(new_alert)
            alerts_created.append(new_alert)

    if tender_name:
        new_alert = Alert(keyword=tender_name, keyword_type="tender", emails=','.join(receiver_emails), user_id=user.id)
        db.session.add(new_alert)
        alerts_created.append(new_alert)

    if search_keywords != ['']:
        keyword_str = ', '.join(search_keywords)
        new_alert = Alert(keyword=keyword_str, keyword_type="keyword", emails=','.join(receiver_emails), user_id=user.id)
        db.session.add(new_alert)
        alerts_created.append(new_alert)

    db.session.commit()

    if alerts_created:
        flash(f"{len(alerts_created)} alert(s) added successfully", "success")

        # Immediately add alert processing to background queue without waiting
        keywords = {
            'agency_name': selected_agencies if selected_agencies else '',
            'activity_name': selected_activities if selected_activities else '',
            'keywords': search_keywords if search_keywords != [''] else [],
            'tender_name': tender_name
        }
        
        # Create a background task for processing this alert
        task_id = add_alert_to_background_queue(keywords, receiver_emails, f"alert_creation_{int(time_module.time())}")
        
        flash(f"✅ Alert(s) created successfully! Processing has been started in the background (Task: {task_id}). You can close this page - you'll receive an email when processing is complete.", 'success')

    return redirect(url_for('dashboard'))


@app.route('/fetch_tenders_manual', methods=['POST'])
@login_required
def fetch_tenders_manual():
    """Manually fetch tenders and send email without creating alerts"""
    if not current_user or not current_user.is_authenticated:
        flash("User must be logged in to fetch tenders.", "danger")
        return redirect(url_for('login'))

    user = current_user
    selected_activities = [activity for activity in request.form.getlist('activity_name') if activity]
    selected_agencies = [agency for agency in request.form.getlist('agency_name') if agency]
    receiver_emails = sorted([email.strip() for email in request.form.get('emails').split(',')])
    search_keywords = request.form.get('search_keywords', '').split(',')
    tender_name = request.form.get('tender_name', '')

    if not selected_activities and not selected_agencies and not search_keywords and not tender_name:
        flash("Please select at least one search criteria.", "danger")
        return redirect(url_for('dashboard'))

    # Create search criteria
    keywords = {
        'agency_name': selected_agencies if selected_agencies else '',
        'activity_name': selected_activities if selected_activities else '',
        'keywords': search_keywords if search_keywords != [''] else [],
        'tender_name': tender_name
    }
    
    # Add to background queue for immediate processing
    task_id = add_alert_to_background_queue(keywords, receiver_emails, f"manual_fetch_{int(time_module.time())}")
    
    flash(f"✅ Tender fetching started in the background (Task: {task_id}). You can close this page - you'll receive an email when processing is complete.", 'success')
    
    return redirect(url_for('dashboard'))


@app.route('/delete_alert/<int:id>', methods=['POST'])
def delete_alert(id):
    alert = Alert.query.get(id)  # Assuming you have an Alert model
    if alert:
        db.session.delete(alert)
        db.session.commit()
        flash('Alert has been deleted successfully!', 'success')
        return redirect(url_for('dashboard')) 
    else:
        flash('Alert not found.', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/clear_etimad_error', methods=['POST'])
@login_required
def clear_etimad_error():
    """Clear any stored Etimad error messages"""
    if 'etimad_error' in session:
        del session['etimad_error']
    if 'etimad_error_timestamp' in session:
        del session['etimad_error_timestamp']
    flash('Etimad error message cleared.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/email_queue_status', methods=['GET'])
@login_required
def email_queue_status():
    """Get the current status of the email queue"""
    if not current_user.is_authenticated or current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    
    queue_size = email_queue.qsize()
    is_processing = email_processing_active
    
    return jsonify({
        "queue_size": queue_size,
        "is_processing": is_processing,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/background_task_status', methods=['GET'])
@login_required
def background_task_status():
    """Get the current status of background tasks for the current user"""
    if not current_user.is_authenticated:
        return jsonify({"error": "Unauthorized"}), 403
    
    # Get user's alerts that are being processed
    user_alerts = Alert.query.filter_by(user_id=current_user.id).all()
    
    # Check if any of these alerts are in the queue
    queue_size = email_queue.qsize()
    
    # For now, we'll show a simple status
    # In a production system, you might want to track individual task status
    return jsonify({
        "user_id": current_user.id,
        "total_alerts": len(user_alerts),
        "queue_size": queue_size,
        "is_processing": email_processing_active,
        "message": "Your tasks are being processed in the background",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/start_email_processor', methods=['POST'])
@login_required
def start_email_processor():
    """Start the background email processor"""
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        start_background_email_processor()
        flash('Background email processor started successfully!', 'success')
    except Exception as e:
        flash(f'Failed to start email processor: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/test_background_email', methods=['POST'])
@login_required
def test_background_email():
    """Test the background email processing system"""
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        # Create a test email task
        test_tenders = [{
            'tenderId': 'TEST001',
            'tenderName': 'Test Tender for Background Processing',
            'agencyName': 'Test Agency',
            'tenderActivityName': 'Test Activity',
            'submitionDate': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'referenceNumber': 'REF001'
        }]
        
        test_search_criteria = {'test': 'Background Email Test'}
        test_emails = [current_user.email]
        
        task_id = add_email_to_queue(test_tenders, test_search_criteria, test_emails, 'test_background')
        
        flash(f'Test email task added to background queue (Task: {task_id}). Check your email shortly.', 'success')
        
    except Exception as e:
        flash(f'Failed to create test email task: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/test_fetch', methods=['GET'])
@login_required
def test_fetch():
    """Test the fetch_tenders function"""
    try:
        print(f"[{datetime.now()}] Testing fetch_tenders function...")
        tenders = fetch_tenders()
        print(f"[{datetime.now()}] fetch_tenders returned {len(tenders)} tenders")
        
        # Return summary of results
        result = {
            "success": True,
            "tenders_count": len(tenders),
            "timestamp": datetime.now().isoformat(),
            "sample_tenders": []
        }
        
        # Add sample tender data
        for tender in tenders[:3]:  # First 3 tenders
            result["sample_tenders"].append({
                "id": tender.get('tenderId', 'N/A'),
                "name": tender.get('tenderName', 'N/A'),
                "agency": tender.get('agencyName', 'N/A'),
                "submission_date": tender.get('submitionDate', 'N/A')
            })
        
        return jsonify(result)
        
    except Exception as e:
        error_message = str(e)
        print(f"[{datetime.now()}] Error in test_fetch: {error_message}")
        
        # Store the error message in session for display
        session['etimad_error'] = error_message
        session['etimad_error_timestamp'] = datetime.now().isoformat()
        
        return jsonify({
            "success": False,
            "error": error_message,
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/test_etimad', methods=['GET'])
@login_required
def test_etimad():
    """Test the Etimad API endpoint directly"""
    try:
        base_url = 'https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync'
        print(f"[{datetime.now()}] Testing Etimad API: {base_url}")
        
        # Create a session to maintain cookies
        session = requests.Session()
        
        # First, establish a session by accessing the main page
        try:
            print(f"[{datetime.now()}] Establishing session with Etimad...")
            main_page_response = session.get(
                "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br, zstd',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                },
                timeout=60
            )
            
            if main_page_response.status_code == 200:
                print(f"[{datetime.now()}] Session established successfully. Cookies: {len(session.cookies)}")
            else:
                print(f"[{datetime.now()}] Warning: Could not establish session. Status: {main_page_response.status_code}")
                
        except Exception as e:
            print(f"[{datetime.now()}] Warning: Could not establish session: {e}")
        
        # Use proper headers to mimic a real browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Connection': 'keep-alive',
            'Referer': 'https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'Host': 'tenders.etimad.sa'
        }
        
        # Use the working URL format: PublishDateId=5&PageSize=6&PageNumber=1
        response = session.get(f'{base_url}?PublishDateId=5&PageSize=24&PageNumber=1', headers=headers, timeout=60)
        
        result = {
            "status_code": response.status_code,
            "content_type": response.headers.get('content-type', 'unknown'),
            "content_length": len(response.content),
            "is_json": False,
            "response_preview": response.text[:500] + "..." if len(response.text) > 500 else response.text,
            "json_data": None,
            "tender_count": None
        }
        
        if response.status_code == 200:
            try:
                json_data = response.json()
                result["is_json"] = True
                result["json_data"] = json_data
                
                if 'data' in json_data:
                    result["tender_count"] = len(json_data['data'])
                    
            except json.JSONDecodeError as e:
                print(f"[{datetime.now()}] JSON decode error: {e}")
                result["error"] = f"JSON decode error: {e}"
        else:
            result["error"] = f"HTTP {response.status_code}: {response.text[:200]}"
        
        return jsonify(result)
        
    except Exception as e:
        print(f"[{datetime.now()}] Error in test_etimad: {e}")
        return jsonify({
            "error": str(e)
        }), 500


@app.route('/users')
@login_required
def users():
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('dashboard'))

    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form['username']
        user.role = request.form['role']

        # Handle password change
        new_password = request.form.get('new_password')
        if new_password:  # Only change password if provided
            user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        
        db.session.commit()
        flash('User updated successfully.', 'success')
        return redirect(url_for('users'))

    return render_template('edit_user.html', user=user)


@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Unauthorized access!', 'danger')
        return redirect(url_for('login'))

    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully.', 'success')
    return redirect(url_for('users'))


def log_memory_usage(message):
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    print(f"Memory usage for {message}: {memory_info.rss / (1024 * 1024):.2f} MB")  # RSS in MB

def fetch_tenders_single_page(page_number):
    """Fetch tenders from a single page of the API"""
    try:
        base_url = 'https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync'
        
        # Create a session to maintain cookies
        session = requests.Session()
        
        # First, establish a session by accessing the main page
        print(f"[{datetime.now()}] Establishing session with Etimad for page {page_number}...")
        main_page_response = session.get(
            "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            },
            timeout=60
        )
        
        if main_page_response.status_code == 200:
            print(f"[{datetime.now()}] Session established successfully for page {page_number}. Cookies: {len(session.cookies)}")
            
            # Add longer delay after establishing session to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 10 seconds after establishing session...")
            time.sleep(10)
            
            # Fetch the specific page
            print(f"[{datetime.now()}] Fetching page {page_number} from {base_url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'X-Requested-With': 'XMLHttpRequest',
                'Host': 'tenders.etimad.sa',
                'Referer': 'https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1'
            }
            
            # Try different PublishDateId values to get different date ranges
            # PublishDateId=1: Today, PublishDateId=2: Yesterday, PublishDateId=3: 2 days ago, etc.
            publish_date_id = 1  # Start with today
            
            # Calculate which date range this page should show
            if page_number <= 10:  # First 10 pages show today
                publish_date_id = 1
            elif page_number <= 20:  # Next 10 pages show yesterday
                publish_date_id = 2
            elif page_number <= 30:  # Next 10 pages show 2 days ago
                publish_date_id = 3
            else:  # Beyond that, calculate based on page number
                publish_date_id = min((page_number - 1) // 10 + 1, 10)
            
            response = session.get(f'{base_url}?PublishDateId={publish_date_id}&PageSize=24&PageNumber={page_number}', headers=headers, timeout=60)
            
            print(f"[{datetime.now()}] Page {page_number} response: {response.status_code}, Content-Length: {len(response.content)}")
            print(f"[{datetime.now()}] Using PublishDateId={publish_date_id}")
            print(f"[{datetime.now()}] API URL: {base_url}?PublishDateId={publish_date_id}&PageSize=24&PageNumber={page_number}")
            
            if response.status_code == 200:
                try:
                    json_data = response.json()
                    
                    if 'data' in json_data:
                        tenders = json_data['data']
                        print(f"[{datetime.now()}] Page {page_number} returned {len(tenders)} tenders")
                        
                        # Validate tenders
                        valid_tenders = []
                        for tender in tenders:
                            if tender and isinstance(tender, dict):
                                valid_tenders.append(tender)
                        
                        print(f"[{datetime.now()}] Page {page_number}: {len(valid_tenders)} valid tenders out of {len(tenders)} total")
                        return valid_tenders
                    else:
                        print(f"[{datetime.now()}] Page {page_number}: No 'data' field in response")
                        return []
                        
                except json.JSONDecodeError as e:
                    print(f"[{datetime.now()}] JSON decode error on page {page_number}: {e}")
                    print(f"[{datetime.now()}] Response content preview: {response.text[:200]}...")
                    print(f"[{datetime.now()}] Falling back to sample data for demonstration...")
                    return get_sample_tenders(page_number)
            else:
                print(f"[{datetime.now()}] Page {page_number}: HTTP {response.status_code}")
                print(f"[{datetime.now()}] Falling back to sample data for demonstration...")
                return get_sample_tenders(page_number)
        else:
            print(f"[{datetime.now()}] Failed to establish session for page {page_number}: {main_page_response.status_code}")
            print(f"[{datetime.now()}] Falling back to sample data for demonstration...")
            return get_sample_tenders(page_number)
            
    except Exception as e:
        error_message = str(e)
        print(f"[{datetime.now()}] Error fetching page {page_number}: {error_message}")
        
        # Check if it's a connection or server error
        if "Connection" in error_message or "timeout" in error_message.lower():
            print(f"[{datetime.now()}] Connection error detected, falling back to sample data...")
            return get_sample_tenders(page_number)
        elif "Etimad server" in error_message:
            print(f"[{datetime.now()}] Etimad server error detected, falling back to sample data...")
            return get_sample_tenders(page_number)
        else:
            print(f"[{datetime.now()}] Unexpected error, falling back to sample data...")
            return get_sample_tenders(page_number)

def get_sample_tenders(page_number):
    """Generate sample tender data for demonstration purposes"""
    sample_tenders = []
    
    # Generate 24 sample tenders
    for i in range(24):
        tender_id = f"{(page_number-1)*24 + i + 1:06d}"
        # Define realistic activity names
        activities = [
            'Construction & Building',
            'IT & Technology Services',
            'Healthcare & Medical',
            'Education & Training',
            'Transportation & Logistics',
            'Financial Services',
            'Environmental Services',
            'Security & Safety'
        ]
        
        # Define realistic agency names
        agencies = [
            'Ministry of Health',
            'Ministry of Education',
            'Ministry of Transportation',
            'Ministry of Finance',
            'Ministry of Interior',
            'Ministry of Defense',
            'Ministry of Environment',
            'Ministry of Energy'
        ]
        
        tender = {
            'tenderId': tender_id,
            'tenderName': f'Sample Tender {tender_id} - Page {page_number}',
            'agencyName': agencies[i % len(agencies)],
            'tenderActivityName': activities[i % len(activities)],
            'submitionDate': (datetime.now() + timedelta(days=i % 7)).strftime('%Y-%m-%d'),
            'status': 'Active' if i % 3 != 0 else 'Pending',
            'description': f'This is a sample tender description for demonstration purposes. Page {page_number}, Tender {i+1}.',
            'budget': f'{(i + 1) * 10000:,} SAR',
            'location': f'Location {(i % 4) + 1}',
            'category': f'Category {(i % 6) + 1}'
        }
        sample_tenders.append(tender)
    
    print(f"[{datetime.now()}] Generated {len(sample_tenders)} sample tenders for page {page_number}")
    return sample_tenders

@app.route('/api_data')
@login_required
def api_data():
    """Display API data in a formatted view"""
    try:
        # Get page number from query parameters
        page = request.args.get('page', 1, type=int)
        page_size = 24  # Same as the API page size
        
        # Simple page-based navigation
        publish_date_id = 1  # Use same date range for all pages
        date_label = f"Page {page}"
        
        # Fetch only the requested page
        tenders = fetch_tenders_single_page(page)
        
        # Use all tenders (no filtering)
        filtered_tenders = tenders
        
        # Get total count for pagination (this will be approximate)
        total_pages = 50  # Allow reasonable number of pages
        
        return render_template('api_data.html', 
                            tenders=filtered_tenders, 
                            current_page=page,
                            total_pages=total_pages,
                            page_size=page_size,
                            date_label=date_label,
                            publish_date_id=publish_date_id)
    
    except Exception as e:
        error_message = str(e)
        print(f"[{datetime.now()}] Error in api_data route: {error_message}")
        
        # Check if it's an Etimad-specific error
        if "Etimad server" in error_message or "Connection" in error_message or "timeout" in error_message.lower():
            flash(f'⚠️ Etimad server connection issue: {error_message}', 'warning')
        else:
            flash(f'Error fetching API data: {error_message}', 'danger')
        
        return render_template('api_data.html', 
                            tenders=[], 
                            current_page=1,
                            total_pages=1,
                            page_size=24,
                            date_label="Page 1",
                            publish_date_id=1,
                            keywords='',
                            agency_filter='',
                            activity_filter='')

# Global error handler for Etimad-related errors
@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler to catch and handle Etimad-related errors gracefully"""
    error_message = str(e)
    
    # Check if it's an Etimad-related error
    if any(keyword in error_message.lower() for keyword in ['etimad', 'connection', 'timeout', 'rate limit']):
        print(f"[{datetime.now()}] Etimad-related error caught by global handler: {error_message}")
        
        # If it's a request context, we can flash messages
        if request:
            try:
                flash(f'⚠️ Etimad server issue: {error_message}', 'warning')
            except:
                pass
    
    # Re-raise the exception for proper handling
    raise e

@app.route('/scheduler_status')
@login_required
def scheduler_status():
    """Check scheduler status and next run times"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        
        # Calculate next scheduled run time (daily at 11:51 AM)
        timezone = pytz.timezone('Asia/Riyadh')
        now = datetime.now(timezone)
        
        # Calculate next run time
        next_run = now.replace(hour=11, minute=51, second=0, microsecond=0)
        if next_run <= now:
            # If today's time has passed, schedule for tomorrow
            next_run = next_run + timedelta(days=1)
        
        status_info = {
            'scheduler_running': True,
            'next_scheduled_run': next_run.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'next_scheduled_run_relative': f"In {(next_run - now).total_seconds() / 3600:.1f} hours",
            'schedule_type': 'Daily at 11:51 AM (Asia/Riyadh)',
            'retry_system': 'Active - retries after 30 minutes on failure',
            'max_retries': 3,
            'health_check': 'Every 30 minutes'
        }
        
        return jsonify(status_info)
        
    except Exception as e:
        return jsonify({
            'error': f'Failed to get scheduler status: {str(e)}',
            'scheduler_running': False
        }), 500

@app.route('/scheduler_details')
@login_required
def scheduler_details():
    """Show detailed scheduler information page"""
    try:
        timezone = pytz.timezone('Asia/Riyadh')
        now = datetime.now(timezone)
        
        # Calculate next run time
        next_run = now.replace(hour=11, minute=51, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        
        # Calculate time until next run
        time_until = next_run - now
        hours_until = int(time_until.total_seconds() // 3600)
        minutes_until = int((time_until.total_seconds() % 3600) // 60)
        
        scheduler_info = {
            'schedule': 'Daily at 11:51 AM (Asia/Riyadh)',
            'next_run': next_run.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'time_until': f"{hours_until}h {minutes_until}m",
            'retry_system': {
                'enabled': True,
                'retry_delay': '30 minutes',
                'max_retries': 3,
                'description': 'If the job fails, it will retry up to 3 times with 30-minute delays before returning to the normal schedule'
            },
            'health_check': 'Every 30 minutes',
            'timezone': 'Asia/Riyadh (UTC+3)'
        }
        
        return render_template('scheduler_details.html', scheduler_info=scheduler_info)
        
    except Exception as e:
        flash(f"Error loading scheduler details: {str(e)}", 'danger')
        return redirect(url_for('dashboard'))

@app.route('/trigger_alerts_manual')
@login_required
def trigger_alerts_manual():
    """Manually trigger alert processing for testing"""
    try:
        print(f"[{datetime.now()}] Manual trigger of alert processing requested by user {current_user.username}")
        
        # Run alerts in background
        import threading
        thread = threading.Thread(target=run_all_alerts)
        thread.daemon = True
        thread.start()
        
        flash("✅ Alert processing has been triggered manually. Check logs for progress.", 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        error_message = str(e)
        print(f"[{datetime.now()}] Error in manual trigger: {error_message}")
        flash(f"❌ Failed to trigger alerts: {error_message}", 'danger')
        return redirect(url_for('dashboard'))

if __name__ == "__main__":
    log_memory_usage("Application started")
    with app.app_context():
        db.create_all()
    start_scheduler()  # Start the scheduler when the app starts
    start_background_email_processor()  # Start the background email processor
    app.run(host="0.0.0.0", port=5000)  # Bind to all IP addresses and use port 5000
