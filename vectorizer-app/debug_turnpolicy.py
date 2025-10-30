#!/usr/bin/env python3
"""
Debug turn policy parameter parsing
"""

import requests
import json
from PIL import Image, ImageDraw
import io

def create_test_image():
    """Create a simple test image"""
    img = Image.new('RGB', (100, 100), color='white')
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 80, 80], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_single_policy(policy):
    """Test a single turn policy"""
    print(f"Testing policy: {policy}")

    image_bytes = create_test_image()

    # Prepare form data
    files = {'file': ('test.png', image_bytes, 'image/png')}

    # This is the key - make sure parameters are JSON string
    params_dict = {
        'potrace': {
            'turnpolicy': policy,
            'turdsize': 2,
            'invert': False,
            'alphamax': 1.0,
            'opticurve': True
        }
    }

    data = {
        'parameters': json.dumps(params_dict),
        'selected_method': 'potrace'
    }

    print(f"  Sending parameters: {data['parameters']}")

    response = requests.post('http://localhost:8000/vectorize', files=files, data=data)

    if response.status_code == 200:
        result = response.json()
        svg = result['vectorized']['potrace']
        params_used = result.get('parameters_used', {})

        print(f"  Response params: {params_used}")
        print(f"  SVG length: {len(svg)}")
        print(f"  SVG paths: {svg.count('<path')}")

        return svg
    else:
        print(f"  Error {response.status_code}: {response.text}")
        return None

if __name__ == '__main__':
    print("🔍 Debugging Turn Policy Parameter...")

    # Test two different policies to see if they produce different results
    svg1 = test_single_policy('black')
    print()
    svg2 = test_single_policy('white')

    if svg1 and svg2:
        if svg1 == svg2:
            print("\n❌ Same output - turn policy not working")
        else:
            print("\n✅ Different output - turn policy working!")