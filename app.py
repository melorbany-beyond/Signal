from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import psutil
import requests

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
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

SENDER_EMAIL = os.getenv('SENDER_EMAIL')
POSTMARK_API_KEY = os.getenv('POSTMARK_API_KEY')

# Global variable to track the current processing page
current_page = 0
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
        return User.query.get(int(user_id))
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
            # Add delay after establishing session to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 5 seconds after establishing session...")
            time.sleep(5)
        else:
            print(f"[{datetime.now()}] Warning: Could not establish session. Status: {main_page_response.status_code}")
            
    except Exception as e:
        print(f"[{datetime.now()}] Warning: Could not establish session: {e}")

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
                time.sleep(2)  # Wait before retrying

        if success and not stop_fetching:
            page_number += 1
            # Add delay between pages to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 10 seconds before fetching next page...")
            time.sleep(10)  # Wait 10 seconds between pages
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

def process_alert(alert):
    keywords = {
        'agency_name': alert.keyword if alert.keyword_type == 'agency' else '',
        'activity_name': alert.keyword if alert.keyword_type == 'activity' else '',
        'tender_name': alert.keyword if alert.keyword_type == 'tender' else '',
        'keywords': alert.keyword.split(',') if alert.keyword_type == 'keyword' else []
    }

    tenders = fetch_tenders()
    filtered_tenders = filter_tenders(tenders, keywords)

    if filtered_tenders:
        receiver_emails = alert.emails.split(',')
        send_email(filtered_tenders, keywords, receiver_emails)
        alert.last_run_date = datetime.utcnow()
        db.session.commit()
        print(f"Email sent for alert ID {alert.id}")
    else:
        print(f"No matching tenders found for alert ID {alert.id}.")

from collections import defaultdict

def run_all_alerts():
    with app.app_context():
        alerts = Alert.query.all()  # Fetch all alerts from the database
        tenders_by_receiver = defaultdict(list)  # Dictionary to group tenders by email receiver

        for alert in alerts:
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
            tenders = fetch_tenders()
            filtered_tenders = filter_tenders(tenders, keywords)

            print(f"[{datetime.now()}] Filtered tenders for alert ID {alert.id}: {len(filtered_tenders)} tenders found.")

            # Add filtered tenders to the appropriate receivers
            if filtered_tenders:
                receiver_emails = alert.emails.split(',')
                for email in receiver_emails:
                    tenders_by_receiver[email].extend(filtered_tenders)  # Group tenders by receiver email

            # Update the last run date for the alert
            alert.last_run_date = datetime.utcnow()
            db.session.commit()

        # Send one grouped email per receiver
        for receiver_email, tenders in tenders_by_receiver.items():
            if tenders:
                print(f"[{datetime.now()}] Preparing email for {receiver_email} with {len(tenders)} tenders.")
                send_email(tenders, {"grouped_alert": "Grouped by Receiver"}, [receiver_email])

        print(f"[{datetime.now()}] Finished processing all alerts.")


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
    trigger = CronTrigger(hour=19, minute=33, timezone=timezone)

    def debug_job():
        logger.info("Scheduler triggered run_all_alerts")
        run_all_alerts()

    scheduler.add_job(func=debug_job, trigger=trigger)
    scheduler.start()
    logger.info("Scheduler started successfully.")


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

        # After creating alerts, proceed to fetch tenders and send email
        keywords = {
            'agency_name': selected_agencies if selected_agencies else '',
            'activity_name': selected_activities if selected_activities else '',
            'keywords': search_keywords if search_keywords != [''] else [],
            'tender_name': tender_name
        }

        tenders = fetch_tenders()
        filtered_tenders = filter_tenders(tenders, keywords)

        if filtered_tenders:
            send_email(filtered_tenders, keywords, receiver_emails)
            flash(f"Successfully sent {len(filtered_tenders)} matching tenders to {', '.join(receiver_emails)}.", 'success')
        else:
            flash("No matching tenders found.", 'warning')

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
@app.route('/scheduler_status', methods=['GET'])
def scheduler_status():
    return jsonify({"status": "Scheduler is running"})

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
        print(f"[{datetime.now()}] Error in test_fetch: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
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
            
            # Add delay after establishing session to avoid bot detection
            print(f"[{datetime.now()}] ⏳ Waiting 3 seconds after establishing session...")
            time.sleep(3)
            
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
        print(f"[{datetime.now()}] Error fetching page {page_number}: {e}")
        print(f"[{datetime.now()}] Falling back to sample data for demonstration...")
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
        flash(f'Error fetching API data: {str(e)}', 'danger')
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

if __name__ == "__main__":
    log_memory_usage("Application started")
    with app.app_context():
        db.create_all()
    start_scheduler()  # Start the scheduler when the app starts
    app.run(host="0.0.0.0", port=5000)  # Bind to all IP addresses and use port 5000
