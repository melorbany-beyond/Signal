#!/usr/bin/env python3
"""
Selenium Test Script for Etimad Bot Detection Bypass
This script tests if Selenium can successfully bypass the bot detection
"""

import os
import sys
import time
import json
from dotenv import load_dotenv

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import the fetch function from app
try:
    from app import fetch_with_selenium, SELENIUM_AVAILABLE
except ImportError as e:
    print(f"Error importing from app: {e}")
    sys.exit(1)

def test_selenium_approach():
    """Test the Selenium approach"""
    
    if not SELENIUM_AVAILABLE:
        print("❌ Selenium is not available. Please install selenium and webdriver-manager")
        return False
    
    print("🧪 Testing Selenium approach for Etimad bot detection bypass...")
    print("=" * 60)
    
    url = 'https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync?page_size=1&pagenumber=1'
    
    print(f"Target URL: {url}")
    print("Starting Selenium WebDriver...")
    
    try:
        start_time = time.time()
        tenders = fetch_with_selenium(url)
        end_time = time.time()
        
        print(f"\n⏱️  Execution time: {end_time - start_time:.2f} seconds")
        
        if tenders:
            print(f"✅ SUCCESS! Retrieved {len(tenders)} tenders")
            print(f"Sample tender data:")
            print(json.dumps(tenders[0], indent=2, ensure_ascii=False))
            return True
        else:
            print("❌ No tenders retrieved")
            return False
            
    except Exception as e:
        print(f"❌ Error during Selenium test: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Selenium Bot Detection Bypass Test")
    print("=" * 60)
    
    # Check if Selenium is available
    if not SELENIUM_AVAILABLE:
        print("❌ Selenium is not available")
        print("Please install required packages:")
        print("pip install selenium webdriver-manager")
        return
    
    print("✅ Selenium is available")
    
    # Run the test
    success = test_selenium_approach()
    
    print("\n" + "=" * 60)
    if success:
        print("🎉 Selenium approach is working!")
        print("You can now use this to bypass bot detection")
    else:
        print("❌ Selenium approach failed")
        print("Check the logs for more details")
    print("=" * 60)

if __name__ == "__main__":
    main()
