#!/usr/bin/env python3
"""
Quick Parameter Test - Validates key parameters are working
Tests the most important parameters for each vectorization method
"""

import asyncio
import io
import hashlib
from PIL import Image, ImageDraw
import sys
import os
sys.path.append(os.path.dirname(__file__))

from main import VectorizerService

async def quick_test():
    """Quick test to verify parameters are working"""
    vectorizer = VectorizerService()

    # Create a simple test image
    img = Image.new('RGB', (100, 100), 'white')
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 80, 80], fill='black')
    draw.ellipse([30, 30, 70, 70], fill='white')  # Create a hole

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    test_image = img_bytes.getvalue()

    def svg_hash(svg):
        return hashlib.md5(svg.encode()).hexdigest()[:8]

    print("Quick Parameter Validation Test")
    print("=" * 40)

    # Test 1: Potrace turdsize parameter
    print("\n1. Testing Potrace turdsize parameter:")
    try:
        svg1 = await vectorizer.potrace_vectorize(test_image, turdsize=0)
        svg2 = await vectorizer.potrace_vectorize(test_image, turdsize=10)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        print(f"   turdsize=0:  {hash1} (length: {len(svg1)})")
        print(f"   turdsize=10: {hash2} (length: {len(svg2)})")

        if hash1 != hash2:
            print("   ✅ PASS: turdsize parameter produces different outputs")
        else:
            print("   ❌ FAIL: turdsize parameter produces identical outputs")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 2: Potrace invert parameter
    print("\n2. Testing Potrace invert parameter:")
    try:
        svg1 = await vectorizer.potrace_vectorize(test_image, invert=False)
        svg2 = await vectorizer.potrace_vectorize(test_image, invert=True)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        print(f"   invert=False: {hash1}")
        print(f"   invert=True:  {hash2}")

        if hash1 != hash2:
            print("   ✅ PASS: invert parameter produces different outputs")
        else:
            print("   ❌ FAIL: invert parameter produces identical outputs")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 3: OpenCV Edge threshold parameters
    print("\n3. Testing OpenCV Edge thresholds:")
    try:
        svg1 = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=30, high_threshold=100)
        svg2 = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=100, high_threshold=200)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        paths1 = svg1.count('<path')
        paths2 = svg2.count('<path')

        print(f"   low thresh:  {hash1} ({paths1} paths)")
        print(f"   high thresh: {hash2} ({paths2} paths)")

        if hash1 != hash2 or paths1 != paths2:
            print("   ✅ PASS: threshold parameters produce different outputs")
        else:
            print("   ❌ FAIL: threshold parameters produce identical outputs")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 4: OpenCV Contour threshold parameter
    print("\n4. Testing OpenCV Contour threshold:")
    try:
        svg1 = await vectorizer.opencv_contour_vectorize(test_image, threshold=50)
        svg2 = await vectorizer.opencv_contour_vectorize(test_image, threshold=200)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        paths1 = svg1.count('<path')
        paths2 = svg2.count('<path')

        print(f"   threshold=50:  {hash1} ({paths1} paths)")
        print(f"   threshold=200: {hash2} ({paths2} paths)")

        if hash1 != hash2 or paths1 != paths2:
            print("   ✅ PASS: threshold parameter produces different outputs")
        else:
            print("   ❌ FAIL: threshold parameter produces identical outputs")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 5: OpenCV Contour invert_threshold parameter
    print("\n5. Testing OpenCV Contour invert_threshold:")
    try:
        svg1 = await vectorizer.opencv_contour_vectorize(test_image, invert_threshold=False)
        svg2 = await vectorizer.opencv_contour_vectorize(test_image, invert_threshold=True)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        print(f"   invert=False: {hash1}")
        print(f"   invert=True:  {hash2}")

        if hash1 != hash2:
            print("   ✅ PASS: invert_threshold parameter produces different outputs")
        else:
            print("   ❌ FAIL: invert_threshold parameter produces identical outputs")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    print("\n" + "=" * 40)
    print("Quick test completed!")

if __name__ == "__main__":
    asyncio.run(quick_test())