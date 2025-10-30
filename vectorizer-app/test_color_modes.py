#!/usr/bin/env python3
"""
Test Potrace color mode functionality
"""

import requests
import json
from PIL import Image, ImageDraw
import io

def create_simple_test_image():
    """Create a simple test image for color testing"""
    img = Image.new('RGB', (100, 100), color='white')
    draw = ImageDraw.Draw(img)

    # Draw a simple black circle
    draw.ellipse([25, 25, 75, 75], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_color_mode(color='#ff0000', fillcolor='transparent', opaque=False, test_name=""):
    """Test a specific color configuration"""
    print(f"Testing {test_name}...")

    image_bytes = create_simple_test_image()

    files = {'file': ('test.png', image_bytes, 'image/png')}
    params_dict = {
        'potrace': {
            'turnpolicy': 'minority',
            'turdsize': 2,
            'invert': False,
            'alphamax': 1.0,
            'opticurve': True,
            'color': color,
            'fillcolor': fillcolor,
            'opaque': opaque
        }
    }
    data = {
        'parameters': json.dumps(params_dict),
        'selected_method': 'potrace'
    }

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        if response.status_code == 200:
            svg = response.json()['vectorized']['potrace']

            # Check if color appears in SVG
            has_color = color.lower() in svg.lower()
            has_fillcolor = fillcolor.lower() in svg.lower() if fillcolor != 'transparent' else 'fill=' in svg

            print(f"  ✅ Success: SVG length {len(svg)}")
            print(f"  Color {color} in SVG: {'✅' if has_color else '❌'}")
            if fillcolor != 'transparent':
                print(f"  Fill color {fillcolor} in SVG: {'✅' if has_fillcolor else '❌'}")

            # Show first 200 chars of SVG to verify colors
            print(f"  SVG preview: {svg[:200]}...")
            return svg
        else:
            print(f"  ❌ API Error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None

def test_all_color_modes():
    """Test various color mode combinations"""
    print("🎨 Testing Potrace Color Modes...")

    # Test 1: Default black
    test_color_mode('#000000', 'transparent', False, "Default Black")
    print()

    # Test 2: Red color
    test_color_mode('#ff0000', 'transparent', False, "Red Stroke")
    print()

    # Test 3: Blue with yellow fill
    test_color_mode('#0000ff', '#ffff00', False, "Blue Stroke + Yellow Fill")
    print()

    # Test 4: Green with opaque
    test_color_mode('#00ff00', 'transparent', True, "Green + Opaque")
    print()

    # Test 5: Purple stroke with red fill and opaque
    test_color_mode('#800080', '#ff0000', True, "Purple Stroke + Red Fill + Opaque")
    print()

    print("🎯 Color mode testing complete!")

if __name__ == '__main__':
    test_all_color_modes()