#!/usr/bin/env python3
"""
Test turn policy parameter to see if it produces different outputs
"""

import requests
import json
from PIL import Image, ImageDraw
import io
import hashlib

def create_ambiguous_image():
    """Create an image with ambiguous pixel arrangements that turn policy affects"""
    img = Image.new('RGB', (200, 100), color='white')
    draw = ImageDraw.Draw(img)

    # Create patterns that have ambiguous turn decisions
    # Diagonal patterns and checkered areas where turn policy matters
    for x in range(50, 150, 8):
        for y in range(20, 80, 8):
            if (x + y) % 16 == 0:
                draw.rectangle([x, y, x+4, y+4], fill='black')

    # Add some diagonal lines
    for i in range(0, 200, 4):
        draw.rectangle([i, i//4, i+2, i//4+2], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

def test_turnpolicy():
    print("🧪 Testing Turn Policy Parameter Effects...")

    image_bytes = create_ambiguous_image()

    policies = ['black', 'white', 'left', 'right', 'minority', 'majority']
    results = {}

    for policy in policies:
        print(f"   Testing policy: {policy}")

        files = {'file': ('test.png', image_bytes, 'image/png')}
        data = {
            'parameters': json.dumps({
                'potrace': {
                    'turnpolicy': policy,
                    'turdsize': 2,
                    'invert': False,
                    'alphamax': 1.0,
                    'opticurve': True
                }
            }),
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

    # Check if different policies produce different results
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

if __name__ == '__main__':
    test_turnpolicy()