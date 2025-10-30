#!/usr/bin/env python3
"""
Test turn policy with truly ambiguous pixel patterns
"""

import requests
import json
from PIL import Image, ImageDraw
import io
import hashlib

def create_ambiguous_image():
    """Create an image with genuinely ambiguous pixel patterns where turn policy matters"""
    img = Image.new('RGB', (100, 100), color='white')
    draw = ImageDraw.Draw(img)

    # Create diagonal checkerboard patterns that create ambiguous corners
    # These patterns are designed to have multiple valid interpretations
    for x in range(0, 100, 2):
        for y in range(0, 100, 2):
            if (x + y) % 4 == 0:
                draw.rectangle([x, y, x+1, y+1], fill='black')

    # Add some more specific ambiguous patterns
    # L-shaped patterns that can be traced multiple ways
    for i in range(20, 80, 8):
        # L-shapes that create ambiguous corners
        draw.rectangle([i, 20, i+2, 22], fill='black')
        draw.rectangle([i, 22, i+1, 24], fill='black')

        draw.rectangle([i, 40, i+1, 42], fill='black')
        draw.rectangle([i+1, 40, i+3, 41], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_turnpolicy_with_ambiguous():
    """Test turn policy with an image designed to have ambiguities"""
    print("🧪 Testing Turn Policy with Ambiguous Patterns...")

    image_bytes = create_ambiguous_image()

    policies = ['black', 'white', 'left', 'right', 'minority', 'majority']
    results = {}

    for policy in policies:
        print(f"   Testing policy: {policy}")

        files = {'file': ('test.png', image_bytes, 'image/png')}
        params_dict = {
            'potrace': {
                'turnpolicy': policy,
                'turdsize': 0,  # Keep all speckles for maximum ambiguity
                'invert': False,
                'alphamax': 0.5,  # Lower threshold for more ambiguous corners
                'opticurve': True
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
                svg_hash = hashlib.md5(svg.encode()).hexdigest()[:8]
                path_count = svg.count('<path')
                results[policy] = {
                    'hash': svg_hash,
                    'paths': path_count,
                    'length': len(svg)
                }
                print(f"     {policy}: {svg_hash} ({path_count} paths, {len(svg)} chars)")
            else:
                print(f"     {policy}: API Error {response.status_code}")
        except Exception as e:
            print(f"     {policy}: Error - {e}")

    # Analyze results
    print("\n📊 Turn Policy Analysis:")
    unique_hashes = set(result['hash'] for result in results.values())

    if len(unique_hashes) > 1:
        print(f"   ✅ SUCCESS: {len(unique_hashes)} different outputs from {len(results)} policies")
        print("   Turn policies are working correctly!")

        # Show which policies produce the same results
        hash_groups = {}
        for policy, result in results.items():
            hash_val = result['hash']
            if hash_val not in hash_groups:
                hash_groups[hash_val] = []
            hash_groups[hash_val].append(policy)

        for hash_val, policies_group in hash_groups.items():
            print(f"   Hash {hash_val}: {', '.join(policies_group)}")
    else:
        print(f"   ❌ ISSUE: All policies produce identical output")
        print("   This suggests turn policy parameter is not affecting the output")

        # Let's also test if it's just that our image doesn't have enough ambiguity
        print(f"   Image may not have sufficient ambiguous patterns.")

if __name__ == '__main__':
    test_turnpolicy_with_ambiguous()