#!/usr/bin/env python3
"""
Test script for Postmark email functionality
"""
import os
from dotenv import load_dotenv
import postmarker

# Load environment variables
load_dotenv()

def test_postmark_connection():
    """Test the Postmark connection and send a test email"""
    try:
        # Get environment variables
        api_key = os.getenv('POSTMARK_API_KEY')
        sender_email = os.getenv('SENDER_EMAIL')
        
        if not api_key:
            print("❌ POSTMARK_API_KEY not found in environment variables")
            return False
            
        if not sender_email:
            print("❌ SENDER_EMAIL not found in environment variables")
            return False
        
        print(f"✅ Found POSTMARK_API_KEY: {api_key[:10]}...")
        print(f"✅ Found SENDER_EMAIL: {sender_email}")
        
        # Test Postmark client connection
        client = postmarker.PostmarkClient(server_token=api_key)
        
        # Try to send a test email (you can change the recipient email)
        test_recipient = input("Enter test recipient email (or press Enter to skip sending): ").strip()
        
        if test_recipient:
            try:
                response = client.emails.send(
                    From=sender_email,
                    To=test_recipient,
                    Subject="Test Email from Signal App",
                    HtmlBody="<h1>Test Email</h1><p>This is a test email to verify Postmark integration is working correctly.</p>"
                )
                print(f"✅ Test email sent successfully!")
                print(f"   Message ID: {response.get('MessageID', 'N/A')}")
                print(f"   To: {test_recipient}")
                return True
            except Exception as e:
                print(f"❌ Failed to send test email: {e}")
                return False
        else:
            print("⏭️  Skipping test email send")
            return True
            
    except Exception as e:
        print(f"❌ Error testing Postmark connection: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing Postmark Integration...")
    print("=" * 40)
    
    success = test_postmark_connection()
    
    print("=" * 40)
    if success:
        print("✅ Postmark integration test completed successfully!")
    else:
        print("❌ Postmark integration test failed!")
