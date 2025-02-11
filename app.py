from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import psutil
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import os
from dotenv import load_dotenv
import time
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content


load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SUPABASE_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

SENDER_EMAIL = os.getenv('SENDER_EMAIL')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD')
SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = int(os.getenv('SMTP_PORT'))

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
    

    now = datetime.now()
    one_day_ago = now - timedelta(days=1)  # Fetch tenders within the last 1 days
    stop_fetching = False  # Flag to stop fetching when tenders older than 1 days are found

    while not stop_fetching:
        retry_count = 0
        success = False

        while retry_count < max_retries:
            try:
                response = requests.get(f'{base_url}?page_size=12&pagenumber={page_number}', timeout=60)
                response.raise_for_status()  # Raise an exception for HTTP errors
                tenders = response.json().get('data', [])

                if not tenders:  # No more tenders found, break the loop
                    return valid_tenders

                # Filter tenders by submission date (within last 7 days)
                for tender in tenders:
                    submission_date = datetime.strptime(tender['submitionDate'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
                    if submission_date >= one_day_ago:  # Only include tenders within the last 7 days
                        valid_tenders.append(tender)
                    else:
                        stop_fetching = True  # Stop fetching if we encounter an older tender
                        break  # Exit the loop early since all subsequent tenders will be older

                success = True
                current_page = page_number  # Update the global page number
                break

            except requests.exceptions.RequestException as e:
                retry_count += 1
                time.sleep(2)  # Wait before retrying

        if success and not stop_fetching:
            page_number += 1
        else:
            break  # Stop fetching if retries are exhausted or there are no tenders

    current_page = 0  # Reset page number after processing
    log_memory_usage("Fetch Tenders.")
    return valid_tenders

# Filter tenders based on keywords
def filter_tenders(tenders, search_criteria):
    filtered_tenders = []
    now = datetime.now()
    one_day_ago = now - timedelta(days=7)

    # Log the search criteria for debugging
    print(f"Search Criteria: {search_criteria}")

    for tender in tenders:
        submission_date_str = tender['submitionDate'].split('.')[0]
        submission_date = datetime.strptime(submission_date_str, "%Y-%m-%dT%H:%M:%S")

        # Skip tenders older than 7 days
        if submission_date < one_day_ago:
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
    subject = "Matching Tenders Found"
    
    # Construct search criteria information for email
    criteria_info = "Search Criteria:\n"
    if search_criteria.get('agency_name'):
        criteria_info += f"Agency Name: {search_criteria['agency_name']}\n"
    if search_criteria.get('activity_name'):
        criteria_info += f"Activity Name: {search_criteria['activity_name']}\n"
    if search_criteria.get('keywords'):
        criteria_info += f"Keywords: {', '.join(search_criteria['keywords'])}\n"
    if search_criteria.get('tender_name'):
        criteria_info += f"Tender Name: {search_criteria['tender_name']}\n"

    body = f"<p><strong>The following tenders matched your criteria:</strong></p><p>{criteria_info}</p><hr>"

    for tender in tenders:
        submission_date_str = tender['submitionDate'].split('.')[0]  # Remove milliseconds
        submission_date = datetime.strptime(submission_date_str, "%Y-%m-%dT%H:%M:%S")
        formatted_submission_date = submission_date.strftime("%Y-%m-%d %H:%M:%S")  # Format as 'YYYY-MM-DD HH:MM:SS'
        
        # Parse and format additional tender details
        last_enqueries_date_str = tender.get('lastEnqueriesDate', '').split('.')[0] if tender.get('lastEnqueriesDate') else "N/A"
        formatted_last_enqueries_date = last_enqueries_date_str if last_enqueries_date_str == "N/A" else datetime.strptime(last_enqueries_date_str, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")

        last_offer_presentation_date_str = tender.get('lastOfferPresentationDate', '').split('.')[0] if tender.get('lastOfferPresentationDate') else "N/A"
        formatted_last_offer_presentation_date = last_offer_presentation_date_str if last_offer_presentation_date_str == "N/A" else datetime.strptime(last_offer_presentation_date_str, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")

        tender_details = f"""
        <p><strong>Tender ID:</strong> {tender['tenderId']}</p>
        <p><strong>Tender Name:</strong> {tender['tenderName']}</p>
        <p><strong>Agency:</strong> {tender['agencyName']}</p>
        <p><strong>Activity:</strong> {tender['tenderActivityName']}</p>
        <p><strong>Submission Date:</strong> {formatted_last_offer_presentation_date}</p>
        <p><strong>Last Enquiries Date:</strong> {formatted_last_enqueries_date}</p>
        <p><strong>Published Date:</strong> {formatted_submission_date}</p>
        <p><strong>Etimad URL:</strong> <a href="https://tenders.etimad.sa/Tender/DetailsForVisitor?STenderId={tender['tenderIdString']}">View Tender</a></p>
        <hr>
        """

        body += tender_details

    # Prepare the SendGrid email with HTML content
    message = Mail(
        from_email=Email(SENDER_EMAIL),
        to_emails=[To(email.strip()) for email in receiver_emails],
        subject=subject,
        html_content=Content("text/html", body)  # HTML content
    )

    try:
        # Send the email using SendGrid API
        sg = sendgrid.SendGridAPIClient(api_key=os.getenv('SENDGRID_API_KEY'))
        response = sg.send(message)
        print(f"[{datetime.now()}] Email sent to {', '.join(receiver_emails)} successfully. Status code: {response.status_code}")
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
    trigger = CronTrigger(hour=8, minute=37, timezone=timezone)

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
if __name__ == "__main__":
    log_memory_usage("Application started")
    with app.app_context():
        db.create_all()
    start_scheduler()  # Start the scheduler when the app starts
    app.run(host="0.0.0.0", port=5000)  # Bind to all IP addresses and use port 5000
