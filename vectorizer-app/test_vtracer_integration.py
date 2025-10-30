#!/usr/bin/env python3
"""
Test VTracer integration with colorful image
"""

import requests
import json
from PIL import Image, ImageDraw
import io
import hashlib

def create_colorful_test_image():
    """Create a colorful test image to test VTracer's color preservation"""
    img = Image.new('RGB', (200, 200), color='white')
    draw = ImageDraw.Draw(img)

    # Create various colored shapes
    draw.ellipse([20, 20, 80, 80], fill='red')
    draw.rectangle([100, 20, 180, 100], fill='blue')
    draw.polygon([(20, 120), (80, 120), (50, 180)], fill='green')
    draw.ellipse([120, 120, 180, 180], fill='orange')

    # Add some text
    try:
        draw.text((60, 90), 'Color!', fill='purple')
    except:
        pass  # Font might not be available

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_vtracer_colors():
    """Test VTracer with color mode"""
    print("🌈 Testing VTracer Color Preservation...")

    image_bytes = create_colorful_test_image()

    files = {'file': ('colorful.png', image_bytes, 'image/png')}
    data = {
        'parameters': json.dumps({
            'vtracer': {
                'colormode': 'color',
                'color_precision': 6,
                'filter_speckle': 4,
                'corner_threshold': 60
            }
        }),
        'selected_method': 'vtracer'
    }

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        if response.status_code == 200:
            result = response.json()

            if 'vectorized' in result and 'vtracer' in result['vectorized']:
                svg = result['vectorized']['vtracer']

                if svg.startswith('Error:'):
                    print(f"❌ VTracer Error: {svg}")
                    return False

                # Count colors in SVG
                fill_count = svg.count('fill="')
                unique_colors = len(set([
                    line.split('fill="')[1].split('"')[0]
                    for line in svg.split('\n')
                    if 'fill="' in line and not line.strip().startswith('<!--')
                ]))

                print(f"✅ VTracer Success!")
                print(f"   SVG length: {len(svg)} characters")
                print(f"   Fill attributes: {fill_count}")
                print(f"   Unique colors: {unique_colors}")
                print(f"   Color preservation: {'✅ YES' if unique_colors > 2 else '❌ NO'}")

                # Save SVG for inspection
                with open('test_vtracer_output.svg', 'w') as f:
                    f.write(svg)
                print(f"   Saved output: test_vtracer_output.svg")

                return unique_colors > 2
            else:
                print(f"❌ No VTracer result in response")
                print(f"   Response keys: {list(result.keys())}")
                return False
        else:
            print(f"❌ API Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Request Error: {e}")
        return False

def test_potrace_comparison():
    """Test same image with Potrace for comparison"""
    print("\n🖤 Testing Potrace for Comparison...")

    image_bytes = create_colorful_test_image()

    files = {'file': ('colorful.png', image_bytes, 'image/png')}
    data = {
        'parameters': json.dumps({
            'potrace': {
                'invert': False,
                'turdsize': 2,
                'turnpolicy': 'minority'
            }
        }),
        'selected_method': 'potrace'
    }

    try:
        response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
        if response.status_code == 200:
            result = response.json()
            svg = result['vectorized']['potrace']

            fill_count = svg.count('fill="')
            print(f"✅ Potrace Success!")
            print(f"   SVG length: {len(svg)} characters")
            print(f"   Fill attributes: {fill_count}")
            print(f"   Colors: Black & white only (as expected)")

            return True
        else:
            print(f"❌ API Error {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Testing VTracer vs Potrace Color Handling")
    print("=" * 60)

    vtracer_success = test_vtracer_colors()
    potrace_success = test_potrace_comparison()

    print("\n" + "=" * 60)
    print("📊 Summary:")
    print(f"   VTracer (Color): {'✅ SUCCESS' if vtracer_success else '❌ FAILED'}")
    print(f"   Potrace (B&W): {'✅ SUCCESS' if potrace_success else '❌ FAILED'}")

    if vtracer_success and potrace_success:
        print("🎉 Both vectorizers working correctly!")
        print("   - VTracer preserves original image colors")
        print("   - Potrace provides classic black & white vectorization")
    else:
        print("⚠️  Some issues detected. Check the output above.")