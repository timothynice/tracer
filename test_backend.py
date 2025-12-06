#!/usr/bin/env python3
"""
Test script to diagnose backend vectorization issues
"""
import requests
import json
import sys
from pathlib import Path

BACKEND_URL = "https://tracer-n32i.onrender.com"

def test_health():
    """Test if backend is healthy"""
    print("Testing backend health...")
    try:
        response = requests.get(f"{BACKEND_URL}/health", timeout=10)
        print(f"✓ Health check: {response.status_code}")
        print(f"  Response: {response.json()}")
        return True
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False

def test_vectorize():
    """Test vectorization with a sample image"""
    print("\nTesting vectorization...")

    # Create a simple test PNG image (10x10 black square)
    try:
        from PIL import Image
        import io

        # Create a simple test image
        img = Image.new('RGB', (100, 100), color='red')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)

        # Prepare the request
        files = {'file': ('test.png', img_bytes, 'image/png')}
        data = {
            'parameters': json.dumps({
                'potrace': {
                    'invert': False,
                    'turdsize': 2,
                    'turnpolicy': 'minority',
                    'alphamax': 1.0,
                    'opticurve': True
                },
                'vtracer': {
                    'colormode': 'color',
                    'color_precision': 6,
                    'filter_speckle': 4,
                    'corner_threshold': 60,
                    'length_threshold': 4.0,
                    'max_iterations': 10,
                    'splice_threshold': 45,
                    'path_precision': 3
                }
            }),
            'selected_method': ''
        }

        print("Sending request...")
        response = requests.post(
            f"{BACKEND_URL}/vectorize",
            files=files,
            data=data,
            timeout=60
        )

        print(f"✓ Status code: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"✓ Success: {result.get('success')}")

            if 'vectorized' in result:
                for method in ['potrace', 'vtracer']:
                    if method in result['vectorized']:
                        content = result['vectorized'][method]
                        if content.startswith('Error:'):
                            print(f"✗ {method}: {content}")
                        elif not content:
                            print(f"✗ {method}: EMPTY RESULT")
                        elif len(content) < 100:
                            print(f"✗ {method}: Result too short ({len(content)} chars)")
                            print(f"    Content: {content}")
                        else:
                            print(f"✓ {method}: {len(content)} chars")
                            # Show first 200 chars
                            print(f"    Preview: {content[:200]}...")
                    else:
                        print(f"✗ {method}: Not in response")
            else:
                print("✗ No 'vectorized' key in response")
                print(f"Response keys: {result.keys()}")

            return True
        else:
            print(f"✗ Error response: {response.text}")
            return False

    except Exception as e:
        print(f"✗ Vectorization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("=" * 60)
    print("Backend Diagnostic Test")
    print("=" * 60)

    # Test health
    health_ok = test_health()

    if not health_ok:
        print("\n⚠️  Backend is not healthy. Check Render logs.")
        return 1

    # Test vectorization
    vectorize_ok = test_vectorize()

    print("\n" + "=" * 60)
    if health_ok and vectorize_ok:
        print("✓ All tests passed!")
        print("\nIf the backend works but frontend shows blank:")
        print("1. Check browser console for JavaScript errors")
        print("2. Check browser network tab for failed requests")
        print("3. Verify VITE_API_URL is set correctly in production build")
        return 0
    else:
        print("✗ Some tests failed. Check Render logs for details.")
        print("\nCommon issues:")
        print("- potrace binary not installed (check Dockerfile)")
        print("- vtracer not installed (pip install vtracer)")
        print("- /tmp directory permissions")
        print("- Memory limits on free tier")
        return 1

if __name__ == "__main__":
    sys.exit(main())
