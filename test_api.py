#!/usr/bin/env python3
"""
Test script for Etimad API endpoint
"""
import requests
import json
from datetime import datetime

def test_etimad_api():
    """Test the Etimad API endpoint"""
    base_url = 'https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync'
    
    print("🧪 Testing Etimad API Endpoint...")
    print("=" * 50)
    
    # Test different URL formats and headers based on the working example
    test_configs = [
        {
            "name": "Exact working format from user example",
            "url": f"{base_url}?PublishDateId=5&PageSize=6&PageNumber=1",
            "headers": {
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
        },
        {
            "name": "With timestamp parameter (like the working example)",
            "url": f"{base_url}?PublishDateId=5&PageSize=6&PageNumber=1&_={int(datetime.now().timestamp() * 1000)}",
            "headers": {
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
        },
        {
            "name": "Try to access the main page first to get cookies",
            "url": "https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=1",
            "headers": {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1'
            }
        }
    ]
    
    # First, try to get a session by accessing the main page
    print("🔐 Attempting to establish session...")
    session = requests.Session()
    
    try:
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
            print(f"✅ Main page accessed successfully. Status: {main_page_response.status_code}")
            print(f"🔑 Cookies obtained: {dict(session.cookies)}")
        else:
            print(f"⚠️ Main page access failed. Status: {main_page_response.status_code}")
            
    except Exception as e:
        print(f"⚠️ Could not access main page: {e}")
    
    # Now test the API endpoints
    for i, config in enumerate(test_configs, 1):
        print(f"\n🔧 Test {i}: {config['name']}")
        print(f"📡 URL: {config['url']}")
        print(f"⏰ Time: {datetime.now()}")
        
        try:
            # Use the session if we have one, otherwise use regular requests
            if 'session' in locals() and session.cookies:
                response = session.get(config['url'], headers=config['headers'], timeout=60)
            else:
                response = requests.get(config['url'], headers=config['headers'], timeout=60)
            
            print(f"📊 Response Status: {response.status_code}")
            print(f"📏 Content Length: {len(response.content)} bytes")
            print(f"🔗 Content Type: {response.headers.get('content-type', 'unknown')}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"✅ JSON parsed successfully")
                    print(f"📋 Data keys: {list(data.keys())}")
                    
                    if 'data' in data:
                        tenders = data['data']
                        print(f"📄 Number of tenders: {len(tenders)}")
                        
                        if tenders:
                            # Show first tender details
                            first_tender = tenders[0]
                            print(f"\n🔍 First Tender Details:")
                            print(f"   ID: {first_tender.get('tenderId', 'N/A')}")
                            print(f"   Name: {first_tender.get('tenderName', 'N/A')}")
                            print(f"   Agency: {first_tender.get('agencyName', 'N/A')}")
                            print(f"   Submission Date: {first_tender.get('submitionDate', 'N/A')}")
                            
                            print(f"\n🎉 SUCCESS! This configuration works!")
                            return config  # Return the working configuration
                            
                        else:
                            print("❌ No tenders found in response")
                            
                    else:
                        print("❌ No 'data' key in response")
                        print(f"📄 Response content: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}...")
                        
                except json.JSONDecodeError as json_error:
                    print(f"❌ JSON parsing error: {json_error}")
                    print(f"📄 Raw response content: {response.text[:500]}...")
            else:
                print(f"❌ HTTP error: {response.status_code}")
                print(f"📄 Error content: {response.text[:500]}...")
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Request error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
    
    print("\n❌ All test configurations failed!")
    return None

if __name__ == "__main__":
    working_config = test_etimad_api()
    
    print("\n" + "=" * 50)
    if working_config:
        print("✅ Found working configuration!")
        print(f"URL: {working_config['url']}")
        print(f"Headers: {working_config['headers']}")
    else:
        print("❌ No working configuration found!")
    print("�� Test completed!")
