#!/usr/bin/env python3
"""
Test script for email functionality
"""
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Mock data for testing
mock_tenders = [
    {
        'tenderId': 12345,
        'tenderName': 'Test Tender for Medical Equipment',
        'agencyName': 'Test Hospital',
        'tenderActivityName': 'Medical Equipment',
        'submitionDate': '2025-08-24T10:00:00.0000000',
        'lastEnqueriesDate': '2025-09-01T14:00:00.0000000',
        'lastOfferPresentationDate': '2025-09-05T16:00:00.0000000',
        'tenderIdString': 'test123',
        'referenceNumber': 'REF-2025-001'
    },
    {
        'tenderId': 12346,
        'tenderName': 'IT Services Contract',
        'agencyName': 'Test University',
        'tenderActivityName': 'Information Technology',
        'submitionDate': '2025-08-24T11:00:00.0000000',
        'lastEnqueriesDate': '2025-09-02T15:00:00.0000000',
        'lastOfferPresentationDate': '2025-09-06T17:00:00.0000000',
        'tenderIdString': 'test456',
        'referenceNumber': 'REF-2025-002'
    }
]

mock_search_criteria = {
    'agency_name': 'Test Hospital',
    'activity_name': 'Medical Equipment',
    'keywords': ['medical', 'equipment', 'hospital']
}

def send_email(tenders, search_criteria, receiver_emails):
    subject = "🎯 New Matching Tenders Found - Signal Alert"
    
    # Create beautiful HTML email template with blue and gold theme
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Signal Tender Alert</title>
        <style>
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                margin: 0; 
                padding: 0; 
                background-color: #f8f9ff; 
                color: #343a40; 
            }
            .email-container { 
                max-width: 600px; 
                margin: 0 auto; 
                background-color: #ffffff; 
                border-radius: 12px; 
                overflow: hidden; 
                box-shadow: 0 10px 25px rgba(1, 5, 174, 0.15); 
            }
            .header { 
                background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
                color: #ffffff; 
                padding: 30px 20px; 
                text-align: center; 
            }
            .header h1 { 
                margin: 0; 
                font-size: 28px; 
                font-weight: 700; 
                color: #ffffff; 
            }
            .header .subtitle { 
                margin: 10px 0 0 0; 
                font-size: 16px; 
                color: #f1c061; 
                font-weight: 500; 
            }
            .content { 
                padding: 30px 20px; 
            }
            .criteria-section { 
                background: linear-gradient(135deg, #f8f9ff 0%, #ffffff 100%); 
                border: 2px solid #0105ae; 
                border-radius: 8px; 
                padding: 20px; 
                margin-bottom: 25px; 
            }
            .criteria-title { 
                color: #0105ae; 
                font-size: 18px; 
                font-weight: 700; 
                margin-bottom: 15px; 
                display: flex; 
                align-items: center; 
            }
            .criteria-title::before { 
                content: "🔍"; 
                margin-right: 10px; 
                font-size: 20px; 
            }
            .criteria-item { 
                background-color: #ffffff; 
                border-left: 4px solid #f1c061; 
                padding: 8px 15px; 
                margin: 8px 0; 
                border-radius: 0 6px 6px 0; 
            }
            .tender-card { 
                background: linear-gradient(135deg, #ffffff 0%, #f8f9ff 100%); 
                border: 1px solid #e9ecef; 
                border-radius: 12px; 
                padding: 20px; 
                margin: 20px 0; 
                box-shadow: 0 4px 6px rgba(1, 5, 174, 0.1); 
                transition: all 0.3s ease; 
            }
            .tender-card:hover { 
                transform: translateY(-2px); 
                box-shadow: 0 8px 15px rgba(1, 5, 174, 0.2); 
            }
            .tender-header { 
                background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
                color: #ffffff; 
                padding: 15px 20px; 
                margin: -20px -20px 20px -20px; 
                border-radius: 12px 12px 0 0; 
            }
            .tender-title { 
                font-size: 18px; 
                font-weight: 700; 
                margin: 0; 
                color: #ffffff; 
            }
            .tender-id { 
                font-size: 14px; 
                color: #f1c061; 
                margin: 5px 0 0 0; 
            }
            .tender-details { 
                display: grid; 
                grid-template-columns: 1fr 1fr; 
                gap: 15px; 
                margin: 15px 0; 
            }
            .detail-item { 
                background-color: #ffffff; 
                padding: 12px; 
                border-radius: 8px; 
                border: 1px solid #e9ecef; 
            }
            .detail-label { 
                font-weight: 600; 
                color: #0105ae; 
                font-size: 12px; 
                text-transform: uppercase; 
                letter-spacing: 0.5px; 
                margin-bottom: 5px; 
            }
            .detail-value { 
                color: #343a40; 
                font-size: 14px; 
                font-weight: 500; 
            }
            .view-button { 
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
            }
            .view-button:hover { 
                background: linear-gradient(135deg, #e6b84c 0%, #d4a73a 100%); 
                transform: translateY(-1px); 
                box-shadow: 0 4px 8px rgba(241, 192, 97, 0.3); 
            }
            .footer { 
                background-color: #f8f9ff; 
                padding: 20px; 
                text-align: center; 
                border-top: 1px solid #e9ecef; 
            }
            .footer-text { 
                color: #6c757d; 
                font-size: 14px; 
                margin: 0; 
            }
            .stats { 
                background: linear-gradient(135deg, #0105ae 0%, #000056 100%); 
                color: #ffffff; 
                padding: 15px 20px; 
                border-radius: 8px; 
                margin-bottom: 20px; 
                text-align: center; 
            }
            .stats-number { 
                font-size: 24px; 
                font-weight: 700; 
                color: #f1c061; 
            }
            .stats-label { 
                font-size: 14px; 
                color: #ffffff; 
                margin-top: 5px; 
            }
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
                <p class="footer-text">Powered by Postmark • {current_time}</p>
            </div>
        </div>
    </body>
    </html>
    """
    
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
    
    print(f"✅ Email template generated successfully!")
    print(f"📧 Subject: {subject}")
    print(f"📊 Tenders: {len(tenders)}")
    print(f"📋 Criteria: {len(criteria_items)} items")
    
    # Save the HTML to a file for preview
    with open('email_preview.html', 'w', encoding='utf-8') as f:
        f.write(body)
    
    print(f"📁 Email preview saved to: email_preview.html")
    print(f"🔑 POSTMARK_API_KEY: {'✅ Set' if os.getenv('POSTMARK_API_KEY') else '❌ Missing'}")
    
    return body

if __name__ == "__main__":
    print("🧪 Testing Email Template Generation...")
    print("=" * 50)
    
    # Test the email function
    email_body = send_email(mock_tenders, mock_search_criteria, ['test@example.com'])
    
    print("\n" + "=" * 50)
    print("✅ Email template test completed!")
    print("📁 Open 'email_preview.html' in your browser to see the email design")

